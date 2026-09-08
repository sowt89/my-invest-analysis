#!/usr/bin/env python3
"""DART 주요계정에서 특정 회사의 계정명·계정ID를 나열한다 (커버리지 공백 진단용).

대상: 금융 3사(레코드 0)와 이력이 짧게 잡힌 회사들. 어떤 이름으로 매출·순이익을
보고하는지 보고 dart_fundamentals.ACCOUNTS의 후보 목록을 보강한다.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dart_fundamentals as D

TARGETS = {"105560": "KB금융", "055550": "신한지주", "000810": "삼성화재",
           "005380": "현대차", "035720": "카카오", "259960": "크래프톤", "068270": "셀트리온"}
YEARS = [("2019", "11013"), ("2024", "11013")]


def main():
    cm = D.corp_map()
    corps = {cm[sc]: n for sc, n in TARGETS.items() if sc in cm}
    for year, reprt in YEARS:
        got = D.major_accounts(list(corps), year, reprt)
        print(f"\n===== {year} 1분기 =====")
        for c, name in corps.items():
            rows, fs = got.get(c, ([], None))
            print(f"\n[{name}] {fs or '응답 없음'} · {len(rows)}행")
            for r in rows:
                if r.get("sj_div") in ("IS", "CIS", "BS"):
                    print(f"   {r.get('sj_div'):3s} {r.get('account_nm',''):30s} id={r.get('account_id','-'):45s} "
                          f"당기 {(r.get('thstrm_amount') or '')[:14]:>14s}  기간 {r.get('thstrm_dt','')}")


if __name__ == "__main__":
    main()
