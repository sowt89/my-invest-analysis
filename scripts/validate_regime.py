#!/usr/bin/env python3
"""시장 국면(상승장·혼조·하락장) 구분이 실제로 의미가 있는지 검증한다.

앱이 쓰는 규칙 (fetch_data.py와 동일)
  상승장 = 기준 지수가 200일선 위  AND  시장 폭 >= 55%
  하락장 = 기준 지수가 200일선 아래 AND  시장 폭 < 45%
  혼조   = 그 밖
  시장 폭 = 워치리스트 종목 중 자기 200일선 위에 있는 비중

주 1회(금요일 기준) 표본을 만들고, 그 시점 이후 3개월(63거래일) 수익률을 본다.
지수(SPY) 수익률과 모멘텀 상위 5종목 균등보유 수익률을 함께 계산한다.
VIX 30 이상 구간도 같은 방식으로 따로 집계한다.

주의: 워치리스트는 2026년 시점에 고른 목록이라 절대 수익률은 부풀려져 있다.
국면 간 '차이'를 보는 용도로만 유효하다.

사용: python3 scripts/validate_regime.py [시작연도] [시장: us|kr]
"""

import os
import statistics as st
import sys

import yfinance as yf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_data import WATCHLIST, WATCHLIST_KR, closes_of, make_session

MARKETS = {"us": {"watchlist": WATCHLIST, "trend": "SPY", "vix": "^VIX"},
           "kr": {"watchlist": WATCHLIST_KR, "trend": "^KS11", "vix": None}}

FWD = 63          # 3개월 (거래일)
MA = 200
TOP_N = 5


def load(session, symbols):
    px = {}
    for s in symbols:
        h = yf.Ticker(s, session=session).history(period="max", interval="1d",
                                                  auto_adjust=True)
        c = closes_of(h)
        if len(c) < MA + FWD:
            continue
        d = [x.strftime("%Y-%m-%d") for x, v in zip(h.index, h["Close"]) if v == v]
        px[s] = dict(zip(d, c))
    return px


def above_ma(series, days, i):
    """i 시점에 자기 200일선 위인가. 자료가 모자라면 None."""
    win = [series[d] for d in days[max(0, i - MA + 1):i + 1] if d in series]
    if len(win) < MA * 0.9 or days[i] not in series:
        return None
    return series[days[i]] > sum(win) / len(win)


def mom_score(series, days, i):
    """앱과 같은 점수 = 0.5 x 12-1 모멘텀 + 0.5 x 200일선 이격도."""
    if i < 252:
        return None
    win = days[i - 251:i + 1]
    if not all(d in series for d in (win[0], win[-1], days[i - 21])):
        return None
    hist = [series[d] for d in win[-MA:] if d in series]
    if len(hist) < MA * 0.9:
        return None
    return (0.5 * (series[days[i - 21]] / series[win[0]] - 1) * 100
            + 0.5 * (series[days[i]] / (sum(hist) / len(hist)) - 1) * 100)


def summarize(label, rows):
    if len(rows) < 20:
        print(f"  {label:26s} 표본 부족 ({len(rows)})")
        return
    spy = [r[0] for r in rows]
    strat = [r[1] for r in rows if r[1] is not None]
    loss = sum(1 for x in spy if x < 0) / len(spy) * 100
    s_txt = f"{st.mean(strat):+6.1f}%" if strat else "   —  "
    print(f"  {label:26s} 표본 {len(rows):4d}  지수 {st.mean(spy):+5.1f}%  "
          f"손실확률 {loss:4.0f}%  중앙값 {st.median(spy):+5.1f}%  전략상위5 {s_txt}")


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "2000"
    cfg = MARKETS[sys.argv[2] if len(sys.argv) > 2 else "us"]
    session = make_session()
    tick = [t for t, _, th in cfg["watchlist"] if th != "지수 ETF"]
    px = load(session, tick + [cfg["trend"]] + ([cfg["vix"]] if cfg["vix"] else []))
    spy = px.pop(cfg["trend"])
    vix = px.pop(cfg["vix"], {}) if cfg["vix"] else {}
    print(f"주가 확보 {len(px)}종목 · 기준지수 {cfg['trend']} {min(spy)}~{max(spy)} · "
          f"변동성지수 {'있음' if vix else '없음'}\n")

    days = sorted(spy)
    idx = {d: i for i, d in enumerate(days)}
    # 주 1회 표본: 각 주의 마지막 거래일
    import datetime as dt
    weekly = {}
    for d in days:
        y, w, _ = dt.date.fromisoformat(d).isocalendar()
        weekly[(y, w)] = d
    samples = [d for d in sorted(weekly.values()) if d[:4] >= start]

    buckets = {"상승장": [], "혼조": [], "하락장": []}
    panic = []
    for d in samples:
        i = idx[d]
        if i + FWD >= len(days):
            continue
        flags = [above_ma(px[t], days, i) for t in px]
        flags = [f for f in flags if f is not None]
        if len(flags) < 10:
            continue
        breadth = sum(flags) / len(flags) * 100
        spy_up = above_ma(spy, days, i)
        if spy_up is None:
            continue
        regime = ("상승장" if spy_up and breadth >= 55 else
                  "하락장" if not spy_up and breadth < 45 else "혼조")
        fwd_spy = (spy[days[i + FWD]] / spy[d] - 1) * 100

        cand = []
        for t in px:
            s = mom_score(px[t], days, i)
            if s is None or d not in px[t] or days[i + FWD] not in px[t]:
                continue
            cand.append((s, (px[t][days[i + FWD]] / px[t][d] - 1) * 100))
        cand.sort(key=lambda x: -x[0])
        fwd_strat = st.mean(r for _, r in cand[:TOP_N]) if len(cand) >= TOP_N * 2 else None

        buckets[regime].append((fwd_spy, fwd_strat))
        if vix.get(d, 0) >= 30:
            panic.append((fwd_spy, fwd_strat))

    total = sum(len(v) for v in buckets.values())
    print(f"평가 구간 {samples[0]} ~ {samples[-1]} · 주간 표본 {total}개 · 이후 3개월 수익률\n")
    for k in ("상승장", "혼조", "하락장"):
        summarize(k, buckets[k])
    print()
    if vix:
        summarize("변동성지수 30 이상 (패닉)", panic)
    print("\n※ 워치리스트는 2026년 시점에 고른 목록 — 전략 절대 수익률은 부풀려져 있다.")
    print("※ 표본이 주 단위로 겹쳐(3개월 구간 중복) 통계적 유의성은 과장된다.")


if __name__ == "__main__":
    main()
