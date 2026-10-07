#!/usr/bin/env python3
"""지수 시가총액 상위 50종목을 기계적으로 뽑아 현재 워치리스트와 비교한다.

규칙(사용자 지정): 지수에서 빠지면 제외 · 시총 50위 밖으로 밀리면 제외 · 50위 안에
들어오면 편입. 사람이 "요즘 뜨는 회사"를 손으로 넣는 여지를 없애 워치리스트에
사후 지식이 쌓이는 것을 막는 것이 목적이다. 모멘텀 순위도 이 50종목 안에서 매긴다.

  나스닥100(QQQ)   상위 50 → fetch_data.WATCHLIST
  코스피(KODEX 200) 상위 50 → fetch_data.WATCHLIST_KR

구성종목 출처
  · 나스닥100: n100tickers(편입·편출 이력)
  · 코스피: 야후 스크리너의 코스피 보통주 시총 순위. 코스피 200이 시총 상위 200으로
    구성되므로 상위 50은 KODEX 200 상위 50과 같다(상장 6개월 미만 종목만 예외).
    야후에 시총이 비는 종목(한국전력)은 DART 주식수 x 현재가로 보완한다.
  · 주식 클래스가 둘인 회사(GOOG/GOOGL)와 우선주는 하나로 세고 순위를 매긴다.

한계: 이 규칙을 과거로 돌려 백테스트할 수는 없다. 지수에서 빠진 종목(인수·상장폐지)의
과거 주가·주식수를 야후 파이낸스가 제공하지 않는다(nasdaq100_membership_report.py 참고).
따라서 "성과가 좋아진다고 검증된 것"이 아니라 "사후 판단이 끼어들지 않게 하는 절차"다.

설치: pip install strictyaml "git+https://github.com/jmccarrell/n100tickers.git"
사용: python3 scripts/index_top_watchlist.py [nasdaq100|kospi200|all] [상위N]
"""

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_data import WATCHLIST, WATCHLIST_KR, make_session

import yfinance as yf

DUP_CLASS = {"GOOG": "GOOGL", "FOX": "FOXA", "NWS": "NWSA"}   # 같은 회사의 다른 주식 클래스
RULE = {"nasdaq100": ("나스닥100", WATCHLIST), "kospi200": ("코스피(KODEX 200)", WATCHLIST_KR)}
DART_PIT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "dart_pit.json")


def members_of(index):
    if index == "nasdaq100":
        try:
            from nasdaq_100_ticker_history import tickers_as_of
        except ImportError:
            raise SystemExit('nasdaq_100_ticker_history가 없습니다: pip install strictyaml '
                             '"git+https://github.com/jmccarrell/n100tickers.git"')
        t = date.today()
        m = set(tickers_as_of(t.year, t.month, t.day))
        return {x for x in m if DUP_CLASS.get(x) not in m}
    # 코스피 보통주 (종목코드 끝자리 0). 우선주(5·7 등)는 뺀다.
    from yfinance import EquityQuery
    q = EquityQuery("and", [EquityQuery("eq", ["region", "kr"]), EquityQuery("eq", ["exchange", "KSC"])])
    rows = []
    for off in range(0, 300, 100):
        got = yf.screen(q, sortField="intradaymarketcap", sortAsc=False, size=100, offset=off).get("quotes", [])
        if not got:
            break
        rows += got
    m = {r["symbol"] for r in rows if r.get("quoteType") == "EQUITY" and r["symbol"].endswith("0.KS")}
    return m | {t for t, _, th in WATCHLIST_KR if th != "지수 ETF"}   # 야후 시총 결측 종목 보완용


def dart_shares():
    try:
        pit = json.load(open(DART_PIT, encoding="utf-8"))
    except OSError:
        return {}
    return {t: next((r["sh"] for r in reversed(rows) if r.get("sh")), None) for t, rows in pit.items()}


def market_caps(session, tickers, index):
    sh = dart_shares() if index == "kospi200" else {}
    def one(t):
        try:
            fi = yf.Ticker(t, session=session).fast_info
            mc = fi.get("marketCap")
            if not mc and sh.get(t) and fi.get("lastPrice"):
                mc = fi["lastPrice"] * sh[t]
            return t, mc
        except Exception:
            return t, None
    with ThreadPoolExecutor(max_workers=8) as ex:
        return {t: mc for t, mc in ex.map(one, sorted(tickers)) if mc}


def report(index, top_n, session):
    label, rule_list = RULE[index]
    members = members_of(index)
    print(f"\n{'=' * 60}\n{label} 구성종목 {len(members)}개 ({date.today()}) · 시총 상위 {top_n} 추출")
    caps = market_caps(session, members, index)
    ranked = sorted(caps.items(), key=lambda kv: -kv[1])
    top = [t for t, _ in ranked[:top_n]]

    cur = {t: n for t, n, th in rule_list if th != "지수 ETF"}
    add = [t for t in top if t not in cur]
    drop = [t for t in cur if t not in top]

    unit = 1e12 if index == "kospi200" else 1e9
    print(f"\n[시총 상위 {top_n}]")
    for i, (t, mc) in enumerate(ranked[:top_n], 1):
        print(f"  {i:2d}. {t:10s} {cur.get(t, '(신규)'):16s} {mc / unit:8.1f}{'조' if unit == 1e12 else 'B'}")
    print(f"\n[빠지는 종목] {len(drop)}개 — {label} 미편입이거나 시총 {top_n}위 밖")
    for t in sorted(drop):
        print(f"  {t:10s} {cur[t]:16s} {label + ' 미편입' if t not in members else f'시총 {top_n}위 밖'}")
    print(f"\n[새로 들어오는 종목] {len(add)}개\n  " + " ".join(sorted(add)))
    print(f"\n요약: 상위 {top_n} 중 유지 {top_n - len(add)} · 제외 {len(drop)} · 신규 {len(add)}")


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    top_n = int(sys.argv[2]) if len(sys.argv) > 2 else 50
    session = make_session()
    for index in (RULE if which == "all" else [which]):
        report(index, top_n, session)
    print("\n※ 이 규칙은 과거 백테스트로 검증할 수 없다(문서 참고). 성과 개선이 아니라")
    print("  워치리스트에 사후 판단이 끼어드는 것을 막는 절차적 장치다.")


if __name__ == "__main__":
    main()
