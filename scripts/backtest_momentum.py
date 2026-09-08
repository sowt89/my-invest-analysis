#!/usr/bin/env python3
"""모멘텀 상위 N종목 전략 백테스트 — 앱이 실제로 쓰는 규칙을 그대로 재현한다.

규칙 (fetch_data.py와 동일)
  점수 = 0.5 x (최근 1개월 제외 12개월 수익률) + 0.5 x (200일선 이격도)
  매월 말 상위 N종목을 균등보유하고 다음 달 말에 교체한다.

비교 대상
  균등보유   : 그달 자격을 갖춘 전 종목 균등보유
  무작위 N   : 같은 종목 수를 무작위로 뽑는 대조군 (시드 200회 평균)
  QQQ        : 나스닥100 매수 후 보유

주의: 워치리스트는 2026년 시점에 사람이 고른 목록이다. 살아남아 성공한
종목이 모여 있으므로 절대 수익률은 부풀려져 있다. 같은 목록 안에서
"고르는 능력"의 비교만 유효하다.

사용: python3 scripts/backtest_momentum.py [상위N] [시작연도]
"""

import os
import random
import statistics as st
import sys
from collections import defaultdict

import yfinance as yf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_data import WATCHLIST, closes_of, make_session

BENCH = "QQQ"
CONTROL_SEEDS = 200
ERAS = [("2000-2007 닷컴버블·회복", "2000", "2008"),
        ("2008-2013 금융위기·회복", "2008", "2014"),
        ("2014-2019 강세장", "2014", "2020"),
        ("2020-2026 코로나 이후", "2020", "2027")]


def load(session, symbols):
    px = {}
    for s in symbols:
        h = yf.Ticker(s, session=session).history(period="max", interval="1d",
                                                  auto_adjust=True)
        c = closes_of(h)
        if len(c) < 300:
            continue
        d = [x.strftime("%Y-%m-%d") for x, v in zip(h.index, h["Close"]) if v == v]
        px[s] = dict(zip(d, c))
    return px


def month_ends(dates):
    last = {}
    for d in dates:
        last[d[:7]] = d
    return [last[m] for m in sorted(last)]


def score_at(series, days, i):
    """점수 = 0.5 x 12-1 모멘텀 + 0.5 x 200일선 이격도 (%)."""
    if i < 252:
        return None
    win = days[i - 251:i + 1]
    if not all(d in series for d in (win[0], win[-1], days[i - 21])):
        return None
    hist = [series[d] for d in win[-200:] if d in series]
    if len(hist) < 180:
        return None
    mom = (series[days[i - 21]] / series[win[0]] - 1) * 100
    dist = (series[days[i]] / (sum(hist) / len(hist)) - 1) * 100
    return 0.5 * mom + 0.5 * dist


def run(px, top_n, start_year):
    tick = [t for t in px if t != BENCH]
    days = sorted({d for s in px.values() for d in s})
    me = [d for d in month_ends(days) if d[:4] >= start_year]
    idx = {d: i for i, d in enumerate(days)}

    rows = []                                   # (월, 전략수익, 균등수익, 무작위수익, QQQ수익)
    for a, b in zip(me, me[1:]):
        i = idx[a]
        cand = []
        for t in tick:
            s = score_at(px[t], days, i)
            if s is None or a not in px[t] or b not in px[t]:
                continue
            cand.append((t, s, (px[t][b] / px[t][a] - 1) * 100))
        if len(cand) < top_n * 2:
            continue
        cand.sort(key=lambda x: -x[1])
        strat = st.mean(r for _, _, r in cand[:top_n])
        equal = st.mean(r for _, _, r in cand)
        rnd = st.mean(st.mean(r for _, _, r in random.Random(k).sample(cand, top_n))
                      for k in range(CONTROL_SEEDS))
        q = ((px[BENCH][b] / px[BENCH][a] - 1) * 100
             if (a in px.get(BENCH, {}) and b in px.get(BENCH, {})) else None)
        rows.append((a, strat, equal, rnd, q, [t for t, _, _ in cand[:top_n]], len(cand)))
    return rows


def grow(rets):
    v = 1.0
    for r in rets:
        v *= 1 + r / 100
    return v


def mdd(rets):
    v, peak, worst = 1.0, 1.0, 0.0
    for r in rets:
        v *= 1 + r / 100
        peak = max(peak, v)
        worst = min(worst, v / peak - 1)
    return worst * 100


def main():
    top_n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    start = sys.argv[2] if len(sys.argv) > 2 else "2000"
    print(f"모멘텀 상위 {top_n}종목 · {start}년~ · 월 1회 교체\n")

    session = make_session()
    syms = [t for t, _, th in WATCHLIST if th != "지수 ETF"] + [BENCH]
    px = load(session, syms)
    print(f"주가 확보 {len(px)}종목 (300일 미만은 제외)\n")

    rows = run(px, top_n, start)
    if not rows:
        print("평가 가능한 구간 없음")
        return
    print(f"평가 구간 {rows[0][0]} ~ {rows[-1][0]} · {len(rows)}개월\n")

    names = ["모멘텀 상위 %d" % top_n, "균등보유", "무작위 %d (대조군)" % top_n]
    series = [[r[1] for r in rows], [r[2] for r in rows], [r[3] for r in rows]]
    q = [r[4] for r in rows if r[4] is not None]

    print(f"{'방식':22s} {'최종':>10s} {'연평균':>8s} {'최대낙폭':>9s}")
    yrs = len(rows) / 12
    for n, s in zip(names, series):
        g = grow(s)
        print(f"{n:22s} {g:9.1f}배 {(g ** (1 / yrs) - 1) * 100:7.1f}% {mdd(s):8.1f}%")
    if len(q) > len(rows) * 0.9:
        g = grow(q)
        print(f"{'QQQ 매수 후 보유':22s} {g:9.1f}배 {(g ** (1 / yrs) - 1) * 100:7.1f}% {mdd(q):8.1f}%")
    else:
        print(f"{'QQQ 매수 후 보유':22s}   구간 일부만 존재 ({len(q)}/{len(rows)}개월) — 비교 생략")

    print("\n[시대별 균등보유 대비 배수]")
    for label, lo, hi in ERAS:
        sub = [r for r in rows if lo <= r[0][:4] < hi]
        if len(sub) < 12:
            continue
        s, e = grow([r[1] for r in sub]), grow([r[2] for r in sub])
        pool = st.mean(r[6] for r in sub)
        print(f"  {label:24s} 전략 {s:6.2f}배 / 균등 {e:5.2f}배 = {s / e:.2f}배"
              f"  (평균 {pool:.0f}종목 중 선택)")

    print("\n[최근 12개월 보유 종목]")
    for r in rows[-12:]:
        print(f"  {r[0][:7]}  {' '.join(r[5])}")

    print("\n※ 워치리스트는 2026년 시점에 고른 목록이라 절대 수익률은 부풀려져 있다.")
    print("※ 거래비용·세금·환율은 반영하지 않았다.")


if __name__ == "__main__":
    main()
