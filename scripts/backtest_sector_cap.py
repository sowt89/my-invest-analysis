#!/usr/bin/env python3
"""테마 집중 한도를 두면 성과가 어떻게 달라지는지 검증한다.

문제: 모멘텀은 같은 테마가 함께 오를 때 그 테마를 통째로 뽑는다. 실제로 지금
상위 5종목 중 4개가 반도체다. 그 테마가 꺾이면 5종목이 같이 빠진다.

검증: 상위 5종목을 고를 때 "한 테마에서 최대 N종목"이라는 한도를 두고,
한도를 넘으면 그 테마의 차순위 대신 다른 테마의 다음 종목을 뽑는다.
한도 없음(현행) · 3 · 2 · 1을 같은 구간에서 비교한다.

한도가 낙폭을 줄이면서 수익률을 크게 깎지 않으면 도입할 가치가 있고,
수익률만 깎으면 현행 유지가 맞다.

사용: python3 scripts/backtest_sector_cap.py [상위N] [시작연도] [시장: us|kr]
"""

import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest_momentum import MARKETS, grow, load, mdd, month_ends, score_at
from fetch_data import make_session

CAPS = [None, 3, 2, 1]          # None = 현행(한도 없음)


def pick(cand, top_n, cap):
    """점수 높은 순으로 뽑되, 한 테마에서 cap개를 넘지 않는다."""
    out, used = [], {}
    for t, theme, score, ret in cand:
        if cap is not None and used.get(theme, 0) >= cap:
            continue
        out.append((t, theme, score, ret))
        used[theme] = used.get(theme, 0) + 1
        if len(out) == top_n:
            break
    return out


def run(px, themes, top_n, start_year, bench, cap):
    """(월, 전략수익, 최대 동일테마 수, 보유종목) 목록."""
    tick = [t for t in px if t != bench]
    days = sorted({d for s in px.values() for d in s})
    me = [d for d in month_ends(days) if d[:4] >= start_year]
    idx = {d: i for i, d in enumerate(days)}

    rows = []
    for a, b in zip(me, me[1:]):
        i = idx[a]
        cand = []
        for t in tick:
            s = score_at(px[t], days, i)
            if s is None or a not in px[t] or b not in px[t]:
                continue
            cand.append((t, themes[t], s, (px[t][b] / px[t][a] - 1) * 100))
        if len(cand) < top_n * 2:
            continue
        cand.sort(key=lambda x: -x[2])
        sel = pick(cand, top_n, cap)
        if len(sel) < top_n:                       # 한도 때문에 못 채우면 그달은 제외
            continue
        cnt = {}
        for _, th, _, _ in sel:
            cnt[th] = cnt.get(th, 0) + 1
        rows.append((a, st.mean(r for _, _, _, r in sel), max(cnt.values()),
                     [t for t, _, _, _ in sel]))
    return rows


def worst_quarter(rets):
    """연속 3개월 최악 수익률 — 테마 쏠림이 터졌을 때의 체감 손실."""
    worst = 0.0
    for i in range(len(rets) - 2):
        g = (grow(rets[i:i + 3]) - 1) * 100
        worst = min(worst, g)
    return worst


def main():
    top_n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    start = sys.argv[2] if len(sys.argv) > 2 else "2000"
    market = sys.argv[3] if len(sys.argv) > 3 else "us"
    cfg = MARKETS[market]
    bench = cfg["bench"]
    themes = {t: th for t, _, th in cfg["watchlist"]}

    print(f"[{market.upper()}] 테마 집중 한도 검증 · 상위 {top_n}종목 · {start}년~ · 월 1회 교체\n")
    session = make_session()
    syms = [t for t, _, th in cfg["watchlist"] if th != "지수 ETF"] + [bench]
    px = load(session, syms)
    print(f"주가 확보 {len(px)}종목\n")

    base = None
    print(f"{'한도':14s} {'최종':>10s} {'연평균':>8s} {'최대낙폭':>9s} {'최악 3개월':>10s} "
          f"{'평균 최대동일테마':>14s} {'개월':>5s}")
    for cap in CAPS:
        rows = run(px, themes, top_n, start, bench, cap)
        if not rows:
            print(f"{'한도 ' + str(cap):14s} 평가 구간 없음")
            continue
        rets = [r[1] for r in rows]
        g, yrs = grow(rets), len(rows) / 12
        label = "한도 없음(현행)" if cap is None else f"한 테마 최대 {cap}"
        print(f"{label:14s} {g:9.1f}배 {(g ** (1 / yrs) - 1) * 100:7.1f}% {mdd(rets):8.1f}% "
              f"{worst_quarter(rets):9.1f}% {st.mean(r[2] for r in rows):13.2f}개 {len(rows):5d}")
        if cap is None:
            base = g

    print("\n[현행 대비 배수]")
    for cap in CAPS[1:]:
        rows = run(px, themes, top_n, start, bench, cap)
        if rows and base:
            print(f"  한 테마 최대 {cap}: {grow([r[1] for r in rows]) / base:.2f}배")

    print("\n※ 워치리스트는 2026년 시점에 고른 목록이라 절대 수익률은 부풀려져 있다.")
    print("※ 한도 때문에 5종목을 못 채우는 달은 그 조건에서 제외했다(개월 수 차이).")


if __name__ == "__main__":
    main()
