#!/usr/bin/env python3
"""생존 편향이 없는 유니버스로 모멘텀 규칙을 재검증한다.

기존 backtest_momentum.py는 사람이 2026년 시점에 고른 워치리스트를 쓴다.
그 목록은 지금까지 살아남아 성공한 종목들로 짜여 있어, 절대 수익률이
부풀려져 있다는 한계가 있다(README 6번 항목).

이 스크립트는 개별 종목 대신 자산군 ETF 15개를 쓴다. ETF 자체는 상장폐지되지
않고(내부 구성종목만 자동으로 교체된다), 오늘 이 목록을 만드는 데 "어떤 종목이
성공했는지"에 대한 사후 지식이 전혀 들어가지 않는다. 15개 모두 1998~2004년에
상장된, 지금도 거래되는 대표 지수 ETF다.

같은 모멘텀 규칙(fetch_data.py·backtest_momentum.py와 동일: 12-1 수익률 +
200일선 이격도, 매월 상위 N개 교체)을 이 유니버스에 적용해, 균등보유·무작위
선택 대비 우위가 여전히 나오는지 본다. 우위가 나오면 모멘텀 효과 자체는
생존 편향의 산물이 아니라는 근거가 되고, 사라지면 워치리스트의 절대 수익률을
그만큼 의심해야 한다.

주의: 자산군 ETF는 개별 종목보다 변동성이 훨씬 작다(분산된 바스켓이므로).
그래서 절대 수익률·낙폭의 크기를 워치리스트 백테스트와 직접 비교하면 안 된다.
여기서 보는 것은 "상대적 우위가 존재하는가"이지 "얼마나 버는가"가 아니다.

사용: python3 scripts/validate_no_survivorship.py [상위N] [시작연도]
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest_momentum import MARKETS, grow, load, mdd, run
from fetch_data import make_session

BENCH = "QQQ"

# 자산군 대표 ETF 15개 — 전부 1998~2004년 상장, 지금도 거래 중, 상장폐지 이력 없음.
# 각 섹터·자산군 내부의 종목 구성은 시간에 따라 바뀌지만(예: XLK가 한때 보유했던
# 회사가 망하면 자동으로 빠진다) ETF 자체는 계속 존재해 생존 편향이 없다.
UNIVERSE = [
    ("XLK", "기술"), ("XLF", "금융"), ("XLE", "에너지"), ("XLV", "헬스케어"),
    ("XLI", "산업재"), ("XLP", "필수소비재"), ("XLY", "임의소비재"),
    ("XLU", "유틸리티"), ("XLB", "소재"),                          # SPDR 9개 섹터 (1998~)
    ("IWM", "미국 소형주"), ("EFA", "선진국(미국 제외)"),
    ("EEM", "신흥국"), ("TLT", "장기국채"), ("GLD", "금"), ("IYR", "리츠"),
]
ERAS = MARKETS["us"]["eras"]   # backtest_momentum.py와 같은 시대 구분


def report(top_n, start):
    session = make_session()
    syms = [t for t, _ in UNIVERSE] + [BENCH]
    px = load(session, syms)
    print(f"생존 편향 없는 유니버스(자산군 ETF) · 상위 {top_n}/{len(UNIVERSE)} · "
          f"{start}년~ · 월 1회 교체\n")
    print(f"주가 확보 {len(px)}/{len(syms)}개 (300일 미만은 제외)\n")

    rows = run(px, top_n, start, BENCH)
    if not rows:
        print("평가 가능한 구간 없음")
        return
    print(f"평가 구간 {rows[0][0]} ~ {rows[-1][0]} · {len(rows)}개월\n")

    names = ["모멘텀 상위 %d" % top_n, "균등보유(15개 전체)", "무작위 %d (대조군)" % top_n]
    series = [[r[1] for r in rows], [r[2] for r in rows], [r[3] for r in rows]]
    q = [r[4] for r in rows if r[4] is not None]
    yrs = len(rows) / 12

    print(f"{'방식':24s} {'최종':>10s} {'연평균':>8s} {'최대낙폭':>9s}")
    for n, s in zip(names, series):
        g = grow(s)
        print(f"{n:24s} {g:9.2f}배 {(g ** (1 / yrs) - 1) * 100:7.1f}% {mdd(s):8.1f}%")
    if len(q) > len(rows) * 0.9:
        g = grow(q)
        print(f"{'QQQ 매수 후 보유':24s} {g:9.2f}배 {(g ** (1 / yrs) - 1) * 100:7.1f}% {mdd(q):8.1f}%")

    print("\n[시대별 균등보유 대비 배수]")
    for label, lo, hi in ERAS:
        sub = [r for r in rows if lo <= r[0][:4] < hi]
        if len(sub) < 12:
            continue
        s, e = grow([r[1] for r in sub]), grow([r[2] for r in sub])
        print(f"  {label:24s} 전략 {s:6.2f}배 / 균등 {e:5.2f}배 = {s / e:.2f}배")

    rnd_g, eq_g = grow(series[2]), grow(series[1])
    strat_g = grow(series[0])
    print(f"\n무작위 대조군({rnd_g:.2f}배) vs 균등보유({eq_g:.2f}배): "
          f"{'거의 같음 → 종목 수 자체는 이득이 아님' if abs(rnd_g/eq_g - 1) < 0.15 else '차이 있음'}")
    print(f"모멘텀 상위({strat_g:.2f}배) vs 균등보유({eq_g:.2f}배): "
          f"{strat_g/eq_g:.2f}배 — {'우위 있음' if strat_g > eq_g * 1.1 else '뚜렷한 우위 없음'}")
    print("\n※ 자산군 ETF는 분산된 바스켓이라 개별 종목보다 변동성이 작다.")
    print("  절대 수익률·낙폭을 워치리스트 백테스트와 직접 비교하지 말 것.")
    print("  여기서 보는 것은 '상대적 우위가 존재하는가'이지 '얼마나 버는가'가 아니다.")


if __name__ == "__main__":
    top_n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    start = sys.argv[2] if len(sys.argv) > 2 else "2000"
    report(top_n, start)
