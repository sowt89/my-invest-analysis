#!/usr/bin/env python3
"""지수(나스닥100 · S&P500) 시가총액 상위 N종목을 기계적으로 뽑아 현재 워치리스트와 비교한다.

규칙(사용자 지정): 지수에서 빠지면 제외 · 시총 N위 밖으로 밀리면 제외 · N위 안에
들어오면 편입. 사람이 "요즘 뜨는 회사"를 손으로 넣는 여지를 없애 워치리스트에
사후 지식이 쌓이는 것을 막는 것이 목적이다. 테마 종목은 이 규칙의 대상이 아니다.

  나스닥100 상위 30 → fetch_data.WATCHLIST_N100
  S&P500  상위 30 → fetch_data.WATCHLIST_SPX  (나스닥 목록과 겹치는 종목은 나스닥 쪽에만 둔다)

주식 클래스가 둘인 회사(GOOG/GOOGL 등)는 하나로 세고 순위를 매긴다.

한계: 이 규칙을 과거로 돌려 백테스트할 수는 없다. 그러려면 매 시점 지수 전 종목의
시가총액이 필요한데, 지수에서 빠진 종목(인수·상장폐지)의 과거 주가·주식수를
야후 파이낸스가 제공하지 않는다(nasdaq100_membership_report.py 문서 참고).
따라서 이 규칙은 "성과가 좋아진다고 검증된 것"이 아니라 "앞으로 사람의 사후 판단이
끼어들지 않게 하는 절차"다.

구성종목 출처: 나스닥100은 n100tickers(편입·편출 이력), S&P500은 datasets/s-and-p-500-companies(현재 목록).
설치: pip install strictyaml "git+https://github.com/jmccarrell/n100tickers.git"
사용: python3 scripts/index_top_watchlist.py [nasdaq100|sp500|all] [상위N]
"""

import csv
import io
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_data import WATCHLIST, WATCHLIST_N100, WATCHLIST_SPX, make_session

import requests
import yfinance as yf

SP500_CSV = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
DUP_CLASS = {"GOOG": "GOOGL", "FOX": "FOXA", "NWS": "NWSA"}   # 같은 회사의 다른 주식 클래스
RULE = {"nasdaq100": ("나스닥100", WATCHLIST_N100), "sp500": ("S&P500", WATCHLIST_SPX)}


def members_of(index):
    if index == "nasdaq100":
        try:
            from nasdaq_100_ticker_history import tickers_as_of
        except ImportError:
            raise SystemExit('nasdaq_100_ticker_history가 없습니다: pip install strictyaml '
                             '"git+https://github.com/jmccarrell/n100tickers.git"')
        t = date.today()
        return set(tickers_as_of(t.year, t.month, t.day))
    r = requests.get(SP500_CSV, timeout=30)
    r.raise_for_status()
    return {row["Symbol"].replace(".", "-") for row in csv.DictReader(io.StringIO(r.text))}


def market_caps(session, tickers):
    def one(t):
        try:
            fi = yf.Ticker(t, session=session).fast_info
            return t, fi.get("marketCap")
        except Exception:
            return t, None
    with ThreadPoolExecutor(max_workers=8) as ex:
        return {t: mc for t, mc in ex.map(one, sorted(tickers)) if mc}


def report(index, top_n, session):
    label, rule_list = RULE[index]
    members = members_of(index)
    members = {t for t in members if DUP_CLASS.get(t) not in members}   # 클래스 중복은 하나만
    print(f"\n{'=' * 60}\n{label} 구성종목 {len(members)}개 ({date.today()}) · 시총 상위 {top_n} 추출")
    caps = market_caps(session, members)
    ranked = sorted(caps.items(), key=lambda kv: -kv[1])
    top = [t for t, _ in ranked[:top_n]]

    cur = {t: n for t, n, th in WATCHLIST if th != "지수 ETF"}
    rule = {t: n for t, n, th in rule_list}
    keep = [t for t in top if t in cur]
    add = [t for t in top if t not in cur]
    drop = [t for t in rule if t not in top]

    B = 1_000_000_000
    print(f"\n[시총 상위 {top_n}]")
    for i, (t, mc) in enumerate(ranked[:top_n], 1):
        print(f"  {i:2d}. {t:6s} {mc / B:8.0f}B  [{'유지' if t in cur else '신규'}]")
    print(f"\n[빠지는 종목] {len(drop)}개 — 규칙 편입분 중 {label} 미편입이거나 시총 {top_n}위 밖 (테마 종목은 대상 아님)")
    for t in sorted(drop):
        print(f"  {t:6s} {rule[t]:24s} {label + ' 미편입' if t not in members else f'시총 {top_n}위 밖'}")
    print(f"\n[새로 들어오는 종목] {len(add)}개\n  " + " ".join(sorted(add)))
    print(f"\n요약: 상위 {top_n} 중 유지 {len(keep)} · 제외 {len(drop)} · 신규 {len(add)}")


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    top_n = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    session = make_session()
    for index in (RULE if which == "all" else [which]):
        report(index, top_n, session)
    print("\n※ 이 규칙은 과거 백테스트로 검증할 수 없다(문서 참고). 성과 개선이 아니라")
    print("  워치리스트에 사후 판단이 끼어드는 것을 막는 절차적 장치다.")


if __name__ == "__main__":
    main()
