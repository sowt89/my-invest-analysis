#!/usr/bin/env python3
"""sec_fundamentals의 분기값 복원 로직 테스트 (합성 데이터, 네트워크 불필요).

개발 환경에서 SEC 접속이 막혀 있어 실데이터로는 검증할 수 없다.
분기 복원은 실수하기 쉬운 부분이므로(중복 집계로 TTM이 2배가 된 적이 있다)
대표 제출 패턴을 합성해 확인한다.

실행: python3 scripts/test_sec_fundamentals.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sec_fundamentals import collect, quarterly, ttm_at

FAIL = []


def check(name, got, want):
    ok = got == want if not isinstance(want, float) else abs(got - want) < 1e-6
    print(f"  {'OK  ' if ok else 'FAIL'} {name}: {got}" + ("" if ok else f" (기대 {want})"))
    if not ok:
        FAIL.append(name)


def facts(tag, recs, unit="USD"):
    return {tag: {"units": {unit: recs}}}


def rec(start, end, val, filed):
    return {"start": start, "end": end, "val": val, "filed": filed}


print("1) 원래 분기값만 제출하는 회사")
f = facts("X", [rec("2024-01-01", "2024-03-31", 10, "2024-04-20"),
                rec("2024-04-01", "2024-06-30", 20, "2024-07-20"),
                rec("2024-07-01", "2024-09-30", 30, "2024-10-20"),
                rec("2024-10-01", "2024-12-31", 40, "2025-01-20")])
q = quarterly(collect(f, ["X"], False))
check("분기 수", len(q), 4)
check("TTM", ttm_at(q, "2025-02-01")[0], 100)

print("\n2) 누적(YTD)으로만 제출하는 회사 — 현금흐름표 패턴")
f = facts("Y", [rec("2024-01-01", "2024-03-31", 10, "2024-04-20"),
                rec("2024-01-01", "2024-06-30", 30, "2024-07-20"),
                rec("2024-01-01", "2024-09-30", 60, "2024-10-20"),
                rec("2024-01-01", "2024-12-31", 100, "2025-01-20")])
q = quarterly(collect(f, ["Y"], False))
check("분기 수", len(q), 4)
check("2분기 차분", q["2024-06-30"][1], 20)
check("4분기 차분", q["2024-12-31"][1], 40)
check("TTM", ttm_at(q, "2025-02-01")[0], 100)

print("\n3) 분기값과 누적값을 함께 제출 — 중복 집계되면 안 된다")
f = facts("Z", [rec("2024-01-01", "2024-03-31", 10, "2024-04-20"),
                rec("2024-04-01", "2024-06-30", 20, "2024-07-20"),
                rec("2024-01-01", "2024-06-30", 30, "2024-07-20"),   # 같은 2분기의 누적본
                rec("2024-07-01", "2024-09-30", 30, "2024-10-20"),
                rec("2024-01-01", "2024-09-30", 60, "2024-10-20"),
                rec("2024-10-01", "2024-12-31", 40, "2025-01-20")])
q = quarterly(collect(f, ["Z"], False))
check("분기 수", len(q), 4)
check("TTM (2배가 되면 실패)", ttm_at(q, "2025-02-01")[0], 100)

print("\n4) 4분기를 따로 안 내는 회사 — 연간에서 역산")
f = facts("W", [rec("2024-01-01", "2024-03-31", 10, "2024-04-20"),
                rec("2024-04-01", "2024-06-30", 20, "2024-07-20"),
                rec("2024-07-01", "2024-09-30", 30, "2024-10-20"),
                rec("2024-01-01", "2024-12-31", 100, "2025-02-15")])  # 연간만
q = quarterly(collect(f, ["W"], False))
check("분기 수", len(q), 4)
check("4분기 역산", q["2024-12-31"][1], 40)

print("\n5) 수정 재제출 — 최초 제출값을 쓴다")
f = facts("V", [rec("2024-01-01", "2024-03-31", 10, "2024-04-20"),
                rec("2024-01-01", "2024-03-31", 99, "2025-04-20")])   # 1년 뒤 수정본
q = quarterly(collect(f, ["V"], False))
check("최초값 유지", q["2024-03-31"][1], 10)

print("\n6) 제출 시점 이전 데이터는 보이지 않는다 (미래 정보 차단)")
f = facts("U", [rec("2024-01-01", "2024-03-31", 10, "2024-04-20"),
                rec("2024-04-01", "2024-06-30", 20, "2024-07-20"),
                rec("2024-07-01", "2024-09-30", 30, "2024-10-20"),
                rec("2024-10-01", "2024-12-31", 40, "2025-01-20")])
q = quarterly(collect(f, ["U"], False))
check("3분기 시점 TTM은 산출 불가", ttm_at(q, "2024-10-25")[0], None)
check("4분기 제출 후 TTM", ttm_at(q, "2025-01-25")[0], 100)

print("\n7) 구멍 있는 4분기는 버린다")
f = facts("T", [rec("2022-01-01", "2022-03-31", 10, "2022-04-20"),
                rec("2024-04-01", "2024-06-30", 20, "2024-07-20"),
                rec("2024-07-01", "2024-09-30", 30, "2024-10-20"),
                rec("2024-10-01", "2024-12-31", 40, "2025-01-20")])
q = quarterly(collect(f, ["T"], False))
check("2년 벌어진 구간 폐기", ttm_at(q, "2025-02-01")[0], None)

print("\n8) 주식수(shares 단위) 수집")
f = facts("S", [{"end": "2024-12-31", "val": 1000, "filed": "2025-01-20"}], unit="shares")
check("shares 단위 수집", collect(f, ["S"], True, "shares")["2024-12-31"][0], 1000.0)
check("USD로 찾으면 없음", collect(f, ["S"], True, "USD"), {})

print("\n9) build() 전체 경로 — 네트워크 대신 합성 companyfacts 주입")
import sec_fundamentals as SF

def quarters(tag, base, filed_lag=20):
    """8분기 제출 이력을 만든다."""
    out = []
    for i in range(8):
        y, q = 2023 + i // 4, i % 4
        st = f"{y}-{q * 3 + 1:02d}-01"
        en = ["03-31", "06-30", "09-30", "12-31"][q]
        end = f"{y}-{en}"
        fm = (q * 3 + 4)
        filed = f"{y + (fm > 12)}-{(fm - 1) % 12 + 1:02d}-{filed_lag}"
        out.append(rec(st, end, base * (i + 1), filed))
    return {tag: {"units": {"USD": out}}}

synth = {"facts": {
    "us-gaap": {**quarters("RevenueFromContractWithCustomerExcludingAssessedTax", 100),
                **quarters("OperatingIncomeLoss", 20),
                **quarters("NetIncomeLoss", 15),
                **quarters("NetCashProvidedByUsedInOperatingActivities", 25),
                **quarters("PaymentsToAcquirePropertyPlantAndEquipment", 5),
                "StockholdersEquity": {"units": {"USD": [
                    {"end": "2024-12-31", "val": 5000, "filed": "2025-01-20"}]}}},
    "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
        {"end": "2024-12-31", "val": 1000, "filed": "2025-01-20"}]}}}}}

SF.get = lambda url: synth                      # 네트워크 차단
rows = SF.build("TEST", 1)
check("레코드 생성", len(rows) > 0, True)
if rows:
    r = rows[-1]
    check("revTtm 존재", r["revTtm"] is not None, True)
    check("niTtm 존재", r["niTtm"] is not None, True)
    check("주식수 반영", r["sh"], 1000.0)
    check("fcfTtm = 영업현금 - capex", r["fcfTtm"], r["cfoTtm"] - r["capexTtm"])
    check("제출일 순서", rows == sorted(rows, key=lambda x: x["filed"]), True)
    check("분기 종료일 중복 없음", len({x["end"] for x in rows}), len(rows))

print("\n" + ("실패 " + str(FAIL) if FAIL else "전부 통과"))
sys.exit(1 if FAIL else 0)
