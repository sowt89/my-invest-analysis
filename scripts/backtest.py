#!/usr/bin/env python3
"""시그널 백테스트 — 단일 종목, 임계값 지정 가능.

사용: python3 scripts/backtest.py [티커] [매수임계값] [매도임계값]
      python3 scripts/backtest.py NVDA 4 -6

매수: 매수 임계값 이상 최초 도달 시 500만원 신규 투입 (연속 지속 시 추가 매수 없음)
매도: 매도 임계값 이하 + 종목이 200일선 아래일 때 전량 청산
체결: 신호 발생 다음 거래일 종가 (룩어헤드 방지)

지표는 scripts/fetch_data.py의 채점 함수를 그대로 사용한다.
과거 재현이 불가능한 지표(FWD PER 괴리·어닝 서프라이즈·공매도 비율)는 0점 처리하므로
실제 화면 점수보다 2~3점 높게 나올 수 있다.
CNN Fear & Greed는 공개된 최근 1년만 실측, 그 이전은 3성분 프록시로 대체한다.
"""

import math
import os
import statistics as st
import sys
from datetime import datetime, timezone

import yfinance as yf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_data import compute_rsi, make_session
from legacy_score import (pt_ret12, pt_drawdown, pt_rsi, pt_volume,
                          pt_spy, pt_vix, pt_fg, band)

LOT = 5_000_000          # 1회 투입액 (원, 환율 무시)
YEARS = 10


def load_prices(session, symbols):
    out = {}
    for sym in symbols:
        h = yf.Ticker(sym, session=session).history(period=f"{YEARS+1}y", interval="1d")
        out[sym] = {
            "close": {d.date().isoformat(): float(c) for d, c in zip(h.index, h["Close"])},
            "vol": {d.date().isoformat(): float(v) for d, v in zip(h.index, h["Volume"])},
        }
        print(f"  {sym}: {len(out[sym]['close'])}일")
    return out


def build_fg(px, days):
    """최근 1년은 CNN 실측, 그 이전은 3성분 z-score 프록시(모멘텀·변동성·안전자산)."""
    actual = {}
    try:
        import requests
        r = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
                         headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                                "Chrome/126.0 Safari/537.36",
                                  "Accept": "application/json"}, timeout=25)
        actual = {datetime.fromtimestamp(p["x"] / 1000, timezone.utc).date().isoformat(): p["y"]
                  for p in r.json()["fear_and_greed_historical"]["data"]}
    except Exception as e:
        print(f"  ! CNN F&G 실측 수집 실패, 전 구간 프록시 사용: {e}")

    spy, vix, tlt = px["SPY"]["close"], px["^VIX"]["close"], px["TLT"]["close"]
    avg = lambda sr, i, n: sum(sr[d] for d in days[max(0, i - n + 1):i + 1]) / len(days[max(0, i - n + 1):i + 1])
    raw = {}
    for i, d in enumerate(days):
        if i < 130:
            continue
        j = days[i - 20]
        raw[d] = (spy[d] / avg(spy, i, 125) - 1,
                  -(vix[d] / avg(vix, i, 50) - 1),
                  (spy[d] / spy[j] - 1) - (tlt[d] / tlt[j] - 1))
    stats = [(st.mean(c), st.pstdev(c)) for c in zip(*raw.values())]
    proxy = {d: max(0, min(100, 50 + st.mean((v - m) / sd for v, (m, sd) in zip(vals, stats)) * 20))
             for d, vals in raw.items()}

    both = [(proxy[d], actual[d]) for d in proxy if d in actual]
    if both:
        n = len(both)
        mp, mv = st.mean(p for p, _ in both), st.mean(a for _, a in both)
        corr = (sum((p - mp) * (a - mv) for p, a in both) / n) / (
            st.pstdev([p for p, _ in both]) * st.pstdev([a for _, a in both]))
        print(f"  F&G 프록시 검증: {n}일 겹침, 상관 {corr:.2f}, "
              f"MAE {st.mean(abs(p - a) for p, a in both):.1f}점")
    return {d: actual.get(d, proxy.get(d)) for d in days}


def daily_scores(px, fg, ticker):
    """거래일별 종합 점수. 과거 재현 불가 지표는 0점."""
    q, spy, vix = px[ticker]["close"], px["SPY"]["close"], px["^VIX"]["close"]
    qv = px[ticker]["vol"]
    days = sorted(set(q) & set(spy) & set(vix) & set(px["TLT"]["close"]))
    out = []
    for i, d in enumerate(days):
        if i < 252:
            continue
        w = days[i - 252:i + 1]
        closes = [q[x] for x in w]
        price = closes[-1]
        rsi = compute_rsi([q[x] for x in days[i - 125:i + 1]])
        vols = [qv[x] for x in days[i - 79:i + 1]]
        recent, prior = vols[-20:], vols[:-20]
        volch = (sum(recent) / len(recent)) / (sum(prior) / len(prior)) * 100 - 100 if prior else None

        spy_w = [spy[x] for x in w]
        raw_mkt = pt_spy((spy_w[-1] / max(spy_w) - 1) * 100) + pt_vix(vix[d]) + pt_fg(fg.get(d))
        adj = math.trunc(raw_mkt / 2) if (spy_w[-1] > sum(spy_w[-200:]) / 200 and raw_mkt < 0) else raw_mkt
        pts = (pt_ret12((price / closes[0] - 1) * 100) + pt_drawdown((price / max(closes) - 1) * 100)
               + pt_rsi(rsi) + pt_volume(volch))
        out.append({"date": d, "price": price, "score": pts + adj,
                    "ma200": sum(closes[-200:]) / 200})
    return out


def simulate(rows, buy_th, sell_th):
    """매수 임계값 최초 도달일 매수 / 매도 임계값 이하 + 200일선 아래일 때 청산."""
    cash_in, shares, trades = 0, 0.0, []
    open_lots, prev_strong, pending = [], False, None
    for r in rows:
        if pending:                                   # 전일 신호 → 오늘 종가 체결
            act = pending
            pending = None
            if act == "buy":
                qty = LOT / r["price"]
                shares += qty
                cash_in += LOT
                open_lots.append({"date": r["date"], "price": r["price"], "qty": qty})
            elif act == "sell" and shares > 0:
                for lot in open_lots:
                    trades.append({**lot, "exit_date": r["date"], "exit_price": r["price"],
                                   "ret": (r["price"] / lot["price"] - 1) * 100,
                                   "pnl": lot["qty"] * (r["price"] - lot["price"])})
                shares, open_lots = 0.0, []

        strong = r["score"] >= buy_th
        if strong and not prev_strong:
            pending = "buy"
        elif shares > 0 and r["score"] <= sell_th and r["price"] < r["ma200"]:
            pending = "sell"
        prev_strong = strong

    last = rows[-1]
    for lot in open_lots:
        trades.append({**lot, "exit_date": None, "exit_price": last["price"],
                       "ret": (last["price"] / lot["price"] - 1) * 100,
                       "pnl": lot["qty"] * (last["price"] - lot["price"])})
    return {"cash_in": cash_in, "trades": trades}


def main():
    ticker = sys.argv[1] if len(sys.argv) > 1 else "QQQ"
    buy_th = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    sell_th = int(sys.argv[3]) if len(sys.argv) > 3 else -6
    print(f"{ticker} 백테스트 (매수 +{buy_th} / 매도 {sell_th} + 200일선)")
    session = make_session()
    px = load_prices(session, [ticker, "SPY", "^VIX", "TLT"])
    days = sorted(set(px[ticker]["close"]) & set(px["SPY"]["close"]) &
                  set(px["^VIX"]["close"]) & set(px["TLT"]["close"]))
    fg = build_fg(px, days)

    cutoff = f"{int(days[-1][:4]) - YEARS}{days[-1][4:]}"
    rows = [r for r in daily_scores(px, fg, ticker) if r["date"] >= cutoff]
    print(f"평가 구간: {rows[0]['date']} ~ {rows[-1]['date']} ({len(rows)}거래일)")

    from collections import Counter
    print("판단 분포:", dict(Counter(band(r["score"], r["price"] >= r["ma200"]) for r in rows)))

    res = simulate(rows, buy_th, sell_th)
    pnl = sum(t["pnl"] for t in res["trades"])
    bh = LOT / rows[0]["price"] * rows[-1]["price"]
    print(f"\n총 투입 {res['cash_in']:,}원 / 손익 {pnl:+,.0f}원 "
          f"({pnl / res['cash_in'] * 100:+.1f}%)" if res["cash_in"] else "\n매수 신호 없음")
    print(f"[비교] 매수 후 보유: {LOT:,}원 → {bh:,.0f}원 ({(bh / LOT - 1) * 100:+.1f}%)")
    for t in res["trades"]:
        print(f"  {t['date']} ${t['price']:.2f} → {t['exit_date'] or '보유중':10s} "
              f"${t['exit_price']:.2f}  {t['ret']:+.1f}%  {t['pnl']:+,.0f}원")


if __name__ == "__main__":
    main()
