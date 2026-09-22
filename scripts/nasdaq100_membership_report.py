#!/usr/bin/env python3
"""워치리스트가 나스닥100에 언제 편입됐는지 감사한다 (생존 편향의 가벼운 대체 검증).

배경: 워치리스트는 2026년 시점에 사람이 고른 목록이라 절대 수익률이 부풀려져
있을 수 있다(README 6번 한계). 이를 완전히 없애려면 '그 시점의 실제 나스닥100
구성종목'으로 백테스트를 다시 짜야 하는데(방법 2), 지수에서 빠진 회사(인수·
상장폐지된 회사)의 과거 주가를 야후 파이낸스가 제공하지 않아(직접 확인:
ALTR·BMC·CA·CELG·CERN·BRCM·YHOO·LNKD 등 10개 표본 중 8개가 데이터 없음)
전체 재구성은 무료 데이터로는 불가능하다.

그 대신 훨씬 가벼운 질문에 답한다: "지금 워치리스트에 있는 종목이 그때도
대형주였는가, 아니면 최근에야 편입된 최근 급등주인가?" 주가를 새로 받을
필요 없이 무료로 공개된 나스닥100 편입·편출 이력(2007~2026년, MIT 라이선스
공개 저장소)만으로 답할 수 있다.

한계: 이건 완전한 생존 편향 보정이 아니다. "그때 이미 대형주였다"는 사실은
"그때도 이 종목을 매수 후보로 골랐을 것이다"를 증명하지 않는다 — 여전히
사후에 봤을 때 성공한 대형주만 고른 것일 수 있다. 여기서 확인하는 것은
목록의 '최신성'뿐이다.

설치 (저장소 표준 의존성이 아니라 이 스크립트 전용):
  pip install strictyaml "git+https://github.com/jmccarrell/n100tickers.git"

사용: python3 scripts/nasdaq100_membership_report.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_data import WATCHLIST

try:
    from nasdaq_100_ticker_history import tickers_as_of
except ImportError:
    raise SystemExit(
        "nasdaq_100_ticker_history가 없습니다. 다음으로 설치하세요:\n"
        '  pip install strictyaml "git+https://github.com/jmccarrell/n100tickers.git"'
    )

FIRST_YEAR = 2007   # 데이터 커버리지 시작 (원본 출처 자체가 이때부터)
LAST_YEAR = 2026


def first_join(ticker):
    """편입 시점을 찾는다. 2007-01-01에 이미 있으면 '2007년 이전부터'."""
    prev = tickers_as_of(FIRST_YEAR, 1, 1)
    if ticker in prev:
        return "2007년 이전부터"
    for y in range(FIRST_YEAR, LAST_YEAR + 1):
        try:
            cur = tickers_as_of(y, 1, 1)
        except NotImplementedError:
            break
        if ticker in cur and ticker not in prev:
            for m in range(1, 13):          # 그 해 안에서 월 단위로 좁힌다
                try:
                    if ticker in tickers_as_of(y, m, 1):
                        return f"{y}-{m:02d}"
                except Exception:
                    pass
            return f"{y}년"
        prev = cur
    now = tickers_as_of(LAST_YEAR, 9, 1)
    return "미편입" if ticker not in now else "편입일 특정 실패"


def main():
    rows = [(t, n, first_join(t)) for t, n, th in WATCHLIST if th != "지수 ETF"]
    pre2007 = [r for r in rows if r[2] == "2007년 이전부터"]
    recent = [r for r in rows if r[2] not in ("2007년 이전부터", "미편입")
              and "특정 실패" not in r[2]]
    never = [r for r in rows if r[2] == "미편입"]
    recent5y = [r for r in recent
                if r[2].startswith(("2021", "2022", "2023", "2024", "2025", "2026"))]

    print(f"워치리스트 {len(rows)}종목 (지수 ETF 제외) · 나스닥100 편입 시점\n")

    print(f"[2007년 이전부터 소속] {len(pre2007)}종목")
    for t, n, _ in sorted(pre2007):
        print(f"  {t:8s} {n}")

    print(f"\n[2007년 이후 편입] {len(recent)}종목")
    for t, n, j in sorted(recent, key=lambda r: r[2]):
        print(f"  {t:8s} {n:24s} {j}")

    print(f"\n[편입 이력 없음 — 다른 거래소 상장이거나 소형/최근 상장] {len(never)}종목")
    for t, n, _ in sorted(never):
        print(f"  {t:8s} {n}")

    print(f"\n요약: {len(rows)}종목 중")
    print(f"  {len(pre2007)}종목({len(pre2007)/len(rows)*100:.0f}%) — 2007년 이전부터 대형주")
    print(f"  {len(recent5y)}종목({len(recent5y)/len(rows)*100:.0f}%) — 최근 5년 안에 나스닥100 편입")
    print(f"  {len(never)}종목({len(never)/len(rows)*100:.0f}%) — 나스닥100 편입 이력 자체가 없음"
          "(다른 거래소 상장 대형주 또는 소형·최근 상장 투기성 종목)")
    print("\n※ '최신성' 감사일 뿐 완전한 생존 편향 보정이 아니다 — 위 문서 참고.")


if __name__ == "__main__":
    main()
