#!/usr/bin/env python3
"""시장 국면 판별 방식들을 같은 데이터로 비교 검증한다.

국면 판별에 흔히 쓰이는 방식을 실시간 계산이 가능한 형태로 구현하고
(미래 정보를 쓰는 사후 구간 설정 방식은 대시보드에 쓸 수 없어 제외),
각 시점 이후 3개월 지수 수익률로 어느 방식이 앞날을 가르는지 본다.

비교 대상
  200일선        지수가 200일 이동평균 위인가 (가장 흔한 기준)
  200일선+기울기  위에 더해 200일선 자체가 상승 중인가
  골든크로스      50일선 > 200일선
  20% 규칙       직전 고점 대비 -20%면 하락장, 직전 저점 대비 +20%면 상승장.
                Lunde-Timmermann(2004)의 20% 기준을 실시간으로 옮긴 것
  10% 규칙       같은 방식, 기준 10%
  12개월 모멘텀   최근 12개월 수익률이 양수인가
  앱 방식        지수 200일선 + 시장 폭(55/45) — 3단계

지수는 편입·폐지가 반영된 지수 자체를 쓴다(생존 편향 없음). 앱 방식의 시장 폭만
현재 워치리스트를 쓰므로 그 항목에는 편향이 있다.

사용: python3 scripts/validate_regime.py [시장: us|kr] [시작연도]
"""

import os
import statistics as st
import sys

import yfinance as yf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_data import WATCHLIST, WATCHLIST_KR, closes_of, make_session

FWD = 63          # 3개월 (거래일)
MA = 200
MARKETS = {
    "us": {"index": "^GSPC", "watchlist": WATCHLIST, "start": "1990"},
    "kr": {"index": "^KS11", "watchlist": WATCHLIST_KR, "start": "1996"},
}


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


def series_of(px, days):
    """일자 축에 맞춘 종가 리스트 (없는 날은 직전 값)."""
    out, last = [], None
    for d in days:
        last = px.get(d, last)
        out.append(last)
    return out


def sma(vals, i, n):
    if i + 1 < n:
        return None
    return sum(vals[i - n + 1:i + 1]) / n


# ---- 판별 방식들: (이름, 함수) · 함수는 i 시점의 상태를 "상승"/"하락"/"중립"으로 준다
def rule_ma200(idx, i, ctx):
    m = sma(idx, i, MA)
    return None if m is None else ("상승" if idx[i] > m else "하락")


def rule_ma200_slope(idx, i, ctx):
    m, m_prev = sma(idx, i, MA), sma(idx, i - 21, MA)
    if m is None or m_prev is None:
        return None
    return "상승" if (idx[i] > m and m > m_prev) else "하락"


def rule_golden(idx, i, ctx):
    f, s = sma(idx, i, 50), sma(idx, i, MA)
    return None if (f is None or s is None) else ("상승" if f > s else "하락")


def swing_state(idx, i, ctx, th):
    """직전 고점 대비 -th% → 하락장, 직전 저점 대비 +th% → 상승장 (상태 유지).

    Lunde-Timmermann(2004)의 20% 규칙을 실시간으로 옮긴 것. 사후 구간 설정과 달리
    미래를 쓰지 않으므로 전환이 늦다.
    """
    key = f"swing{th}"
    if key not in ctx:
        ctx[key] = {"state": "상승", "peak": idx[0], "trough": idx[0], "at": 0}
    s = ctx[key]
    while s["at"] < i:                      # 앞 시점부터 순차 갱신 (i는 항상 증가)
        s["at"] += 1
        p = idx[s["at"]]
        s["peak"], s["trough"] = max(s["peak"], p), min(s["trough"], p)
        if s["state"] == "상승" and p <= s["peak"] * (1 - th / 100):
            s["state"], s["trough"] = "하락", p
        elif s["state"] == "하락" and p >= s["trough"] * (1 + th / 100):
            s["state"], s["peak"] = "상승", p
    return s["state"]


def rule_swing20(idx, i, ctx):
    return swing_state(idx, i, ctx, 20)


def rule_swing10(idx, i, ctx):
    return swing_state(idx, i, ctx, 10)


def rule_mom12(idx, i, ctx):
    if i < 252:
        return None
    return "상승" if idx[i] > idx[i - 252] else "하락"


def rule_app(idx, i, ctx):
    """앱 방식: 지수 200일선 + 시장 폭 55/45 (3단계)."""
    m = sma(idx, i, MA)
    br = ctx["breadth"].get(i)
    if m is None or br is None:
        return None
    if idx[i] > m and br >= 55:
        return "상승"
    if idx[i] <= m and br < 45:
        return "하락"
    return "중립"


RULES = [("200일선", rule_ma200), ("200일선+기울기", rule_ma200_slope),
         ("골든크로스", rule_golden), ("20% 규칙", rule_swing20),
         ("10% 규칙", rule_swing10), ("12개월 모멘텀", rule_mom12),
         ("앱 방식(폭 포함)", rule_app)]


def report(name, states, fwd, switches):
    """상태별 이후 3개월 지수 수익률·손실확률과 분리력을 출력한다."""
    by = {}
    for s, r in zip(states, fwd):
        if s is not None:
            by.setdefault(s, []).append(r)
    if "상승" not in by or "하락" not in by:
        print(f"  {name:16s} 표본 부족")
        return None
    line, loss = [], {}
    for s in ("상승", "중립", "하락"):
        v = by.get(s)
        if not v:
            continue
        loss[s] = sum(1 for x in v if x < 0) / len(v) * 100
        line.append(f"{s} {st.mean(v):+5.1f}%/손실{loss[s]:3.0f}%({len(v)})")
    sep = loss["하락"] - loss["상승"]
    print(f"  {name:16s} {' · '.join(line)}  ▶ 분리력 {sep:+5.1f}%p · 전환 {switches}회")
    return sep


def main():
    mkt = sys.argv[1] if len(sys.argv) > 1 else "us"
    cfg = MARKETS[mkt]
    start = sys.argv[2] if len(sys.argv) > 2 else cfg["start"]
    session = make_session()
    tick = [t for t, _, th in cfg["watchlist"] if th != "지수 ETF"]
    px = load(session, [cfg["index"]] + tick)
    idx_px = px.pop(cfg["index"])
    days = sorted(idx_px)
    idx = [idx_px[d] for d in days]
    print(f"{mkt.upper()} · 지수 {cfg['index']} {days[0]}~{days[-1]} · "
          f"시장 폭용 종목 {len(px)}개\n")

    # 시장 폭(자기 200일선 위 비중) — 앱 방식에만 쓴다. 누적합으로 이동평균을 굴린다.
    above = [0] * len(days)      # 200일선 위 종목 수
    total = [0] * len(days)      # 200일치 자료가 있는 종목 수
    for t in px:
        v = series_of(px[t], days)
        first = next((i for i, x in enumerate(v) if x is not None), None)
        if first is None:
            continue
        cum = [0.0]
        for x in v[first:]:
            cum.append(cum[-1] + x)
        for i in range(first + MA - 1, len(days)):
            k = i - first + 1
            m = (cum[k] - cum[k - MA]) / MA
            total[i] += 1
            above[i] += v[i] > m
    breadth = {i: above[i] / total[i] * 100 for i in range(len(days)) if total[i] >= 10}

    # 주 1회(주의 마지막 거래일) 표본
    import datetime as dt
    weekly = {}
    for j, d in enumerate(days):
        y, w, _ = dt.date.fromisoformat(d).isocalendar()
        weekly[(y, w)] = j
    samples = [j for j in sorted(weekly.values())
               if days[j][:4] >= start and j + FWD < len(days)]
    fwd = [(idx[j + FWD] / idx[j] - 1) * 100 for j in samples]
    print(f"평가 구간 {days[samples[0]]} ~ {days[samples[-1]]} · 주간 표본 {len(samples)}개 "
          f"· 이후 3개월 지수 수익률\n")

    ctx = {"breadth": breadth}
    results = []
    for name, fn in RULES:
        states = [fn(idx, j, ctx) for j in samples]
        sw = sum(1 for a, b in zip(states, states[1:]) if a and b and a != b)
        results.append((name, report(name, states, fwd, sw)))

    # 변동성지수 30 이상(패닉) 구간 — 미국만 (야후에 VKOSPI가 없다)
    if mkt == "us":
        vix = load(session, ["^VIX"]).get("^VIX", {})
        if vix:
            v = series_of(vix, days)
            panic = [(j, r) for j, r in zip(samples, fwd) if (v[j] or 0) >= 30]
            if len(panic) >= 20:
                pr = [r for _, r in panic]
                print(f"\n  변동성지수 30 이상   {st.mean(pr):+5.1f}%/손실"
                      f"{sum(1 for x in pr if x < 0) / len(pr) * 100:3.0f}%({len(pr)})"
                      "  ▶ 패닉 뒤 3개월은 오히려 반등이 잦다")

    best = max((r for r in results if r[1] is not None), key=lambda r: r[1], default=None)
    if best:
        print(f"\n분리력(하락장 손실확률 − 상승장 손실확률)이 가장 큰 방식: {best[0]} {best[1]:+.1f}%p")
    print("\n※ 3개월 구간이 주 단위로 겹쳐 통계적 유의성은 과장된다.")
    print("※ 시장 폭은 현재 워치리스트 기준이라 그 항목만 생존 편향이 있다.")


if __name__ == "__main__":
    main()
