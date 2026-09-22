#!/usr/bin/env python3
"""나스닥100 시가총액 상위 50종목을 기계적으로 뽑아 현재 워치리스트와 비교한다.

규칙(사용자 지정): 나스닥100에서 빠지면 제외 · 시총 50위 밖으로 밀리면 제외 ·
50위 안에 들어오면 편입. 사람이 "요즘 뜨는 회사"를 손으로 넣는 여지를 없애
워치리스트에 사후 지식이 쌓이는 것을 막는 것이 목적이다.

한계: 이 규칙을 과거로 돌려 백테스트할 수는 없다. 그러려면 매 시점 나스닥100
전 종목의 시가총액이 필요한데, 지수에서 빠진 종목(인수·상장폐지)의 과거
주가·주식수를 야후 파이낸스가 제공하지 않는다(nasdaq100_membership_report.py
문서 참고). 따라서 이 규칙은 "성과가 좋아진다고 검증된 것"이 아니라
"앞으로 사람의 사후 판단이 끼어들지 않게 하는 절차"다.

설치: pip install strictyaml "git+https://github.com/jmccarrell/n100tickers.git"
사용: python3 scripts/nasdaq100_top50_watchlist.py [상위N]
"""

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_data import WATCHLIST, WATCHLIST_N100, make_session

import yfinance as yf

try:
    from nasdaq_100_ticker_history import tickers_as_of
except ImportError:
    raise SystemExit(
        "nasdaq_100_ticker_history가 없습니다:\n"
        '  pip install strictyaml "git+https://github.com/jmccarrell/n100tickers.git"'
    )


def market_caps(session, tickers):
    caps = {}
    for i, t in enumerate(sorted(tickers), 1):
        try:
            info = yf.Ticker(t, session=session).info
            mc = info.get("marketCap")
            if mc:
                caps[t] = (mc, info.get("shortName") or info.get("longName") or "")
        except Exception:
            pass
        if i % 20 == 0:
            print(f"  시총 수집 {i}/{len(tickers)}...", flush=True)
    return caps


def main():
    top_n = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    today = date.today()
    members = tickers_as_of(today.year, today.month, today.day)
    print(f"나스닥100 구성종목 {len(members)}개 ({today}) · 시총 상위 {top_n} 추출\n")

    session = make_session()
    caps = market_caps(session, members)
    ranked = sorted(caps.items(), key=lambda kv: -kv[1][0])
    top = [t for t, _ in ranked[:top_n]]

    cur = {t: n for t, n, th in WATCHLIST if th != "지수 ETF"}
    rule = {t: n for t, n, th in WATCHLIST_N100}   # 규칙으로 들어온 종목만 제외 후보
    keep = [t for t in top if t in cur]
    add = [t for t in top if t not in cur]
    drop = [t for t in rule if t not in top]

    B = 1_000_000_000
    print(f"\n[시총 상위 {top_n} — 새 워치리스트] {len(top)}종목")
    for i, (t, (mc, name)) in enumerate(ranked[:top_n], 1):
        mark = "유지" if t in cur else "신규"
        print(f"  {i:2d}. {t:6s} {name[:28]:30s} {mc / B:8.0f}B  [{mark}]")

    print(f"\n[그대로 남는 종목] {len(keep)}개")
    print("  " + " ".join(sorted(keep)))

    print(f"\n[빠지는 종목] {len(drop)}개 — 규칙 편입분 중 나스닥100 미편입이거나 시총 {top_n}위 밖 (테마 종목은 대상 아님)")
    for t in sorted(drop):
        reason = "나스닥100 미편입" if t not in members else f"시총 {top_n}위 밖"
        print(f"  {t:6s} {rule[t]:24s} {reason}")

    print(f"\n[새로 들어오는 종목] {len(add)}개")
    print("  " + " ".join(sorted(add)))

    print(f"\n요약: 현재 {len(cur)}종목 → 새 규칙 {len(top)}종목 "
          f"(유지 {len(keep)} · 제외 {len(drop)} · 신규 {len(add)})")
    print("\n※ 이 규칙은 과거 백테스트로 검증할 수 없다(문서 참고). 성과 개선이 아니라")
    print("  워치리스트에 사후 판단이 끼어드는 것을 막는 절차적 장치다.")


if __name__ == "__main__":
    main()
