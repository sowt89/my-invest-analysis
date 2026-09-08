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


def full_probe():
    """전체 재무제표·주식수 응답 원형 확인 — 현대차 순이익이 안 잡히는 이유, 신한지주 주식수 0의 원인."""
    cm = D.corp_map()
    for sc, name, year, reprt in (("005380", "현대차", "2019", "11013"), ("005380", "현대차", "2020", "11011"),
                                  ("105560", "KB금융", "2021", "11013")):
        rows = D.full_statement(cm[sc], year, reprt)
        print(f"\n[{name} {year} {reprt}] 전체 재무제표 {len(rows)}행 · 키: {sorted(rows[0]) if rows else '-'}")
        for r in rows:
            if r.get("sj_div") in ("IS", "CIS"):
                print(f"   {r.get('sj_div'):3s} {r.get('account_nm',''):34s} id={r.get('account_id','-'):50s} "
                      f"당기 {(r.get('thstrm_amount') or '')[:14]:>14s} 누적 {(r.get('thstrm_add_amount') or '')[:14]:>14s} "
                      f"기간 {r.get('thstrm_dt','')}")
    for sc, name in (("055550", "신한지주"), ("105560", "KB금융")):
        d = json.loads(D.get("stockTotqySttus.json", corp_code=cm[sc], bsns_year="2024", reprt_code="11011"))
        print(f"\n[{name} 2024 주식수] status={d.get('status')}")
        for r in d.get("list") or []:
            print(f"   se={r.get('se')!r} istc_totqy={r.get('istc_totqy')} rcept={r.get('rcept_no')}")


def build_probe(stock_code):
    """한 회사만 전체 수집 과정을 돌려 지표별로 잡힌 분기(시작~종료, 제출일)를 나열한다."""
    from datetime import date
    cm = D.corp_map()
    corp = cm[stock_code]
    flows = {k: {} for k in D.FLOW_KEYS}
    inst = {k: {} for k in ("eq", "ca", "cl", "ltd")}
    for year in range(D.FIRST_YEAR, date.today().year + 1):
        for reprt in D.REPORTS:
            rows, _ = D.major_accounts([corp], year, reprt).get(corp, ([], None))
            if rows:
                D.ingest(rows, D.filed_of(rows[0].get("rcept_no"), year, reprt), flows, inst)
            if D.lacks(rows):
                full = D.full_statement(corp, year, reprt)
                src = "전체" if full else "없음"
                if full:
                    st, en = D.PERIOD[reprt]
                    D.ingest(full, D.filed_of(full[0].get("rcept_no"), year, reprt), flows, inst,
                             period=(f"{year}-{st}", f"{year}-{en}"))
                print(f"  {year} {reprt}: 주요계정 {len(rows)}행 → 보강 {src}", flush=True)
    for k in ("rev", "ni"):
        q = D.quarterly(flows[k])
        print(f"\n[{stock_code}] {k} 원시 {len(flows[k])}건 → 분기 {len(q)}개")
        for (st, en), (v, f) in sorted(flows[k].items()):
            print(f"   원시 {st}~{en}  {v:>18,.0f}  제출 {f}")
        for en in sorted(q):
            print(f"   분기 {q[en][0]}~{en}  {q[en][1]:>18,.0f}  제출 {q[en][2]}")


if __name__ == "__main__":
    if "--build" in sys.argv:
        build_probe(sys.argv[sys.argv.index("--build") + 1])
        sys.exit()
    if "--full" in sys.argv:
        full_probe()
        sys.exit()
    main()
