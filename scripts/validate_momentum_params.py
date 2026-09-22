#!/usr/bin/env python3
"""모멘텀 규칙이 특정 설정에서만 좋은 건지(과최적화) 검증한다.

앱이 쓰는 설정은 "12개월 모멘텀(최근 1개월 제외) 50% + 200일선 이격도 50%,
상위 5종목, 월 1회 교체"다. 이 조합이 우연히 잘 맞은 것인지, 근처 설정도
비슷하게 좋은지 본다.

  · 근처 설정도 다 좋다 → 규칙이 튼튼하다
  · 이 조합만 유독 좋다 → 과거 데이터에 맞춰 끼워 맞춘 것(과최적화)을 의심

바꿔보는 것
  모멘텀 기간   3 · 6 · 9 · 12개월 (최근 1개월 제외는 그대로)
  가중치        모멘텀만(1.0) · 반반(0.5) · 200일선만(0.0)
  보유 종목 수  2 · 3 · 5 · 7 · 10
  교체 주기     1 · 2 · 3개월

전체 조합(180가지)을 모두 돌린 뒤, 현행 설정이 그중 몇 등인지 본다.
현행이 1등이면 오히려 의심스럽고, 중상위권이면서 대부분의 조합이 지수를
이기면 규칙 자체가 튼튼하다는 뜻이다.

사용: python3 scripts/validate_momentum_params.py [시작연도] [시장: us|kr]
"""

import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest_momentum import MARKETS, grow, load, mdd, month_ends
from fetch_data import make_session

LOOKBACKS = [3, 6, 9, 12]           # 개월
WEIGHTS = [1.0, 0.5, 0.0]           # 모멘텀 비중 (나머지는 200일선 이격도)
TOP_NS = [2, 3, 5, 7, 10]
FREQS = [1, 2, 3]                   # 교체 주기 (개월)
DEFAULT = (12, 0.5, 5, 1)           # 앱의 현행 설정
MA = 200
SKIP = 21                           # 최근 1개월 제외 (거래일)


def precompute(px, days, me_idx, tick):
    """월말마다 각 종목의 모멘텀(기간별)과 200일선 이격도를 미리 계산한다."""
    mom = {m: {} for m in LOOKBACKS}
    dist = {}
    for t in tick:
        s = px[t]
        for i in me_idx:
            hist = [s[d] for d in days[max(0, i - MA + 1):i + 1] if d in s]
            if len(hist) >= MA * 0.9 and days[i] in s:
                dist[(t, i)] = (s[days[i]] / (sum(hist) / len(hist)) - 1) * 100
            for m in LOOKBACKS:
                back = i - (m * 21)
                if back < 0 or i - SKIP < 0:
                    continue
                d0, d1 = days[back], days[i - SKIP]
                if d0 in s and d1 in s:
                    mom[m][(t, i)] = (s[d1] / s[d0] - 1) * 100
    return mom, dist


def run(px, days, me, me_idx, tick, bench, mom, dist, look, w, top_n, freq):
    """(전략 월수익 목록). 교체 주기 freq 개월마다 상위 top_n을 새로 뽑는다."""
    rets, held = [], []
    for k, (a, b) in enumerate(zip(me, me[1:])):
        i = me_idx[k]
        if k % freq == 0 or not held:
            cand = []
            for t in tick:
                mv, dv = mom[look].get((t, i)), dist.get((t, i))
                if mv is None or dv is None or a not in px[t]:
                    continue
                cand.append((w * mv + (1 - w) * dv, t))
            if len(cand) < top_n * 2:
                held = []
                continue
            cand.sort(reverse=True)
            held = [t for _, t in cand[:top_n]]
        r = [(px[t][b] / px[t][a] - 1) * 100 for t in held
             if a in px[t] and b in px[t]]
        if len(r) == len(held) and held:
            rets.append(st.mean(r))
    return rets


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "2000"
    market = sys.argv[2] if len(sys.argv) > 2 else "us"
    cfg = MARKETS[market]
    bench = cfg["bench"]

    print(f"[{market.upper()}] 모멘텀 규칙 민감도 검증 · {start}년~\n")
    session = make_session()
    syms = [t for t, _, th in cfg["watchlist"] if th != "지수 ETF"] + [bench]
    px = load(session, syms)
    tick = [t for t in px if t != bench]
    days = sorted({d for s in px.values() for d in s})
    me_all = month_ends(days)
    idx = {d: i for i, d in enumerate(days)}
    me = [d for d in me_all if d[:4] >= start]
    me_idx = [idx[d] for d in me]
    print(f"주가 확보 {len(px)}종목 · 월말 {len(me)}개\n")

    mom, dist = precompute(px, days, me_idx, tick)

    def cagr(rets):
        if len(rets) < 24:
            return None
        return (grow(rets) ** (12 / len(rets)) - 1) * 100

    # 지수 기준선
    bq = [(px[bench][b] / px[bench][a] - 1) * 100 for a, b in zip(me, me[1:])
          if a in px[bench] and b in px[bench]]
    bench_cagr = cagr(bq)

    results = {}
    for look in LOOKBACKS:
        for w in WEIGHTS:
            for n in TOP_NS:
                for f in FREQS:
                    rets = run(px, days, me, me_idx, tick, bench, mom, dist, look, w, n, f)
                    c = cagr(rets)
                    if c is not None:
                        results[(look, w, n, f)] = (c, mdd(rets), grow(rets))

    def show(title, keys, fmt):
        print(f"\n[{title}]  (나머지는 현행 설정 고정)")
        print(f"  {'설정':22s} {'연평균':>8s} {'최대낙폭':>9s} {'최종':>10s}")
        for k in keys:
            if k not in results:
                continue
            c, m, g = results[k]
            mark = "  ← 현행" if k == DEFAULT else ""
            print(f"  {fmt(k):22s} {c:7.1f}% {m:8.1f}% {g:9.1f}배{mark}")

    L, W, N, F = DEFAULT
    show("모멘텀 기간", [(l, W, N, F) for l in LOOKBACKS], lambda k: f"{k[0]}개월 모멘텀")
    show("가중치", [(L, w, N, F) for w in WEIGHTS],
         lambda k: {1.0: "모멘텀만", 0.5: "반반(현행)", 0.0: "200일선만"}[k[1]])
    show("보유 종목 수", [(L, W, n, F) for n in TOP_NS], lambda k: f"상위 {k[2]}종목")
    show("교체 주기", [(L, W, N, f) for f in FREQS], lambda k: f"{k[3]}개월마다 교체")

    # 과최적화 점검: 현행 설정이 전체 조합 중 몇 등인가
    ranked = sorted(results.items(), key=lambda kv: -kv[1][0])
    rank = [k for k, _ in ranked].index(DEFAULT) + 1
    cagrs = [v[0] for v in results.values()]
    beat = sum(1 for c in cagrs if bench_cagr and c > bench_cagr)
    print(f"\n[과최적화 점검] 전체 {len(results)}개 조합")
    print(f"  현행 설정(12개월·반반·상위5·월1회): 연평균 {results[DEFAULT][0]:.1f}% · "
          f"{rank}등 / {len(results)}")
    print(f"  전체 조합 연평균: 중앙값 {st.median(cagrs):.1f}% · "
          f"최저 {min(cagrs):.1f}% · 최고 {max(cagrs):.1f}%")
    if bench_cagr:
        print(f"  지수({bench}) 연평균 {bench_cagr:.1f}% · "
              f"이를 이긴 조합 {beat}/{len(results)}개 ({beat / len(results) * 100:.0f}%)")
    print(f"\n  상위 5개 조합:")
    for k, v in ranked[:5]:
        print(f"    {k[0]:2d}개월 · 가중치 {k[1]:.1f} · 상위 {k[2]:2d} · {k[3]}개월교체"
              f"  연평균 {v[0]:5.1f}%")
    print(f"  하위 3개 조합:")
    for k, v in ranked[-3:]:
        print(f"    {k[0]:2d}개월 · 가중치 {k[1]:.1f} · 상위 {k[2]:2d} · {k[3]}개월교체"
              f"  연평균 {v[0]:5.1f}%")

    print("\n※ 워치리스트 편향 때문에 절대 수익률은 부풀려져 있다. 조합 간 비교만 유효하다.")
    print("※ 거래비용·세금 미반영 — 교체가 잦은 조합일수록 실제로는 더 불리하다.")


if __name__ == "__main__":
    main()
