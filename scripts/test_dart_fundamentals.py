#!/usr/bin/env python3
"""dart_fundamentals의 조립 로직 테스트 (합성 응답, 네트워크 불필요).

개발 환경에서 DART 접속이 막혀 있어 API 응답을 흉내 내 검증한다.
확인: 1) 분기·누적·연간이 섞인 손익 → 정확한 TTM  2) 현금흐름 누적 차분
      3) 4분기 역산  4) account_id 없이 계정명으로 대체  5) 보통주 주식수
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dart_fundamentals as D

FAIL = []


def check(name, got, want):
    ok = got == want if not isinstance(want, float) else (got is not None and abs(got - want) < 1e-6)
    print(f"  {'OK  ' if ok else 'FAIL'} {name}: {got}" + ("" if ok else f" (기대 {want})"))
    if not ok:
        FAIL.append(name)


Q = {"11013": ("01-01", "03-31", "03-31"), "11012": ("04-01", "06-30", "06-30"),
     "11014": ("07-01", "09-30", "09-30"), "11011": ("01-01", "12-31", "12-31")}
RCPT = {"11013": "0515", "11012": "0814", "11014": "1114", "11011": "0315"}   # 접수 월일 (사업보고서는 이듬해)


def synth(year, reprt, use_ids=True, rev_q=100.0):
    """한 보고서의 fnlttSinglAcntAll 응답. 분기 매출 rev_q, 영업이익 20, 순이익 15, 현금흐름 누적."""
    st, en, bs = Q[reprt]
    n = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}[reprt]
    fy = year + 1 if reprt == "11011" else year
    rc = f"{fy}{RCPT[reprt]}0000000"
    period = f"{year}.{st.replace('-', '.')} ~ {year}.{en.replace('-', '.')}"
    def row(sj, aid, nm, cur, cum=None, dt=period):
        r = {"sj_div": sj, "account_id": aid if use_ids else "-standard-", "account_nm": nm,
             "thstrm_amount": str(cur), "thstrm_dt": dt, "rcept_no": rc}
        if cum is not None:
            r["thstrm_add_amount"] = str(cum)
        return r
    if reprt == "11011":                                  # 사업보고서: 연간값만
        is_rows = [row("IS", "ifrs-full_Revenue", "매출액", rev_q * 4),
                   row("IS", "dart_OperatingIncomeLoss", "영업이익", 80.0),
                   row("IS", "ifrs-full_ProfitLoss", "당기순이익", 60.0)]
    else:
        is_rows = [row("IS", "ifrs-full_Revenue", "매출액", rev_q, rev_q * n),
                   row("IS", "dart_OperatingIncomeLoss", "영업이익", 20.0, 20.0 * n),
                   row("IS", "ifrs-full_ProfitLoss", "분기순이익", 15.0, 15.0 * n)]
    ytd = f"{year}.01.01 ~ {year}.{en.replace('-', '.')}"
    cf_rows = [row("CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities", "영업활동현금흐름", 25.0 * n, dt=ytd),
               row("CF", "ifrs-full_PurchaseOfPropertyPlantAndEquipment", "유형자산의 취득", 5.0 * n, dt=ytd)]
    bsd = f"{year}.{bs.replace('-', '.')}"
    bs_rows = [row("BS", "ifrs-full_Equity", "자본총계", 5000.0, dt=bsd),
               row("BS", "ifrs-full_CurrentAssets", "유동자산", 800.0, dt=bsd),
               row("BS", "ifrs-full_CurrentLiabilities", "유동부채", 400.0, dt=bsd),
               row("BS", "ifrs-full_LongtermBorrowings", "장기차입금", 300.0, dt=bsd),
               row("BS", "ifrs-full_BondsIssued", "사채", 200.0, dt=bsd)]
    return {"status": "000", "list": is_rows + cf_rows + bs_rows}, rc


def fake_get(endpoint, **p):
    year, reprt = int(p.get("bsns_year", 0)), p.get("reprt_code")
    if endpoint.startswith("fnlttMultiAcnt"):
        corps = p["corp_code"].split(",")
        if year not in (2023, 2024):
            return json.dumps({"status": "013", "list": []}).encode()
        rows = []
        for c in corps:
            d, _ = synth(year, reprt, use_ids=fake_get.use_ids)
            for r in d["list"]:
                if r["sj_div"] == "CF":            # 주요계정에는 현금흐름이 없다
                    continue
                if c == "C0000003" and r["account_nm"] == "매출액":   # 주요계정에 매출이 빠진 회사
                    continue
                rows.append({**r, "corp_code": c, "fs_div": "CFS"})
        return json.dumps({"status": "000", "list": rows}).encode()
    if endpoint.startswith("fnlttSinglAcntAll"):
        fake_get.full_calls += 1
        if year not in (2023, 2024) or p["fs_div"] != "CFS":
            return json.dumps({"status": "013", "list": []}).encode()
        d, _ = synth(year, reprt, use_ids=fake_get.use_ids)
        for r in d["list"]:
            r.pop("thstrm_dt")                     # 전체 재무제표 응답에는 기간 필드가 없다
        return json.dumps(d).encode()
    if endpoint.startswith("stockTotqySttus"):
        if year not in (2023, 2024) or reprt != "11011":
            return json.dumps({"status": "013"}).encode()
        _, rc = synth(year, reprt)
        return json.dumps({"status": "000", "list": [
            {"se": "보통주", "istc_totqy": "1,000", "rcept_no": rc},
            {"se": "우선주", "istc_totqy": "100", "rcept_no": rc}]}).encode()
    raise AssertionError(endpoint)


fake_get.use_ids = True
fake_get.full_calls = 0
D.get = fake_get
D.FIRST_YEAR = 2023

print("1) account_id로 매칭 · 2023~2024 8개 보고서 · 회사 2곳 일괄")
built, calls = D.build_all(["A0000001", "B0000002"], 2024)
rows = built["A0000001"]
check("회사 2곳 모두 조립", sorted(built), ["A0000001", "B0000002"])
check("레코드 수 (TTM 가능 시점)", len(rows), 5)
last = rows[-1]
check("매출 TTM = 4분기 합", last["revTtm"], 400.0)
check("영업이익 TTM", last["opTtm"], 80.0)
check("순이익 TTM", last["niTtm"], 60.0)
check("현금흐름은 주요계정에 없어 결측", last["fcfTtm"], None)
check("자본총계", last["eq"], 5000.0)
check("장기차입금 + 사채", last["ltd"], 500.0)
check("보통주 주식수 (우선주 제외)", last["sh"], 1000.0)
check("1년 전 매출 TTM", last["revTtmPrev"], 400.0)
check("제출일 순서", rows == sorted(rows, key=lambda r: r["filed"]), True)
check("사업보고서 접수일 = 이듬해", any(r["filed"].startswith("2025") for r in rows), True)
check("호출 수 절감 (재무 8회 + 주식수 4회)", calls, 12)
check("매출·순이익이 다 있으면 전체 재무제표를 부르지 않는다", fake_get.full_calls, 0)

print("\n2) 4분기 역산 — 사업보고서(연간)만 있는 4분기")
first_full = next(r for r in rows if r["end"] == "2023-12-31")
check("2023 연간 TTM", first_full["revTtm"], 400.0)

print("\n3) account_id 없이 계정명으로 대체")
fake_get.use_ids = False
rows2 = D.build_all(["A0000001"], 2024)[0]["A0000001"]
check("계정명 매칭으로 같은 결과", rows2[-1]["revTtm"], 400.0)
check("순이익도 계정명으로", rows2[-1]["niTtm"], 60.0)
fake_get.use_ids = True

print("\n4) 계정명 정규화 — 공백 차이")
check("'기타 유동부채'≠'유동부채' (부분 일치 금지)",
      D.pick([{"account_nm": "기타 유동부채", "account_id": "x"}], [], ["유동부채"]), None)
check("공백 무시 일치",
      D.pick([{"account_nm": "영업이익 (손실)", "account_id": "x"}], [], ["영업이익(손실)"]) is not None, True)

print("\n4-1) account_id 접두어 차이 · IS/CIS 중복")
check("ifrs_ 접두어(옛 표기)도 매칭",
      D.pick([{"account_nm": "지배기업소유주지분", "account_id": "ifrs_ProfitLossAttributableToOwnersOfParent"}],
             ["ifrs-full_ProfitLossAttributableToOwnersOfParent"], []) is not None, True)
check("표준코드 미사용 표기는 id 매칭 안 함", D.norm_id("-표준계정코드 미사용-"), None)
dup = [{"sj_div": "IS", "account_nm": "연결당기순이익", "account_id": "-", "thstrm_amount": "10"},
       {"sj_div": "CIS", "account_nm": "연결당기순이익", "account_id": "-", "thstrm_amount": "99"}]
check("IS와 CIS에 같은 이름이면 앞선 IS 행", D.pick(dup, *D.ACCOUNTS["ni"])["thstrm_amount"], "10")

print("\n5) 주요계정에 매출이 빠진 회사 — 전체 재무제표로 보강")
rows3 = D.build_all(["C0000003"], 2024)[0]["C0000003"]
check("보강 호출: 8개 보고서 x 1회 (CFS에서 찾음)", fake_get.full_calls, 8)
check("보강한 매출 TTM", rows3[-1]["revTtm"], 400.0)
check("주요계정 값은 유지 (순이익)", rows3[-1]["niTtm"], 60.0)
check("기간 필드 없이 사업보고서 4분기 역산", next(r for r in rows3 if r["end"] == "2023-12-31")["revTtm"], 400.0)

print("\n6) 매출 계정이 아예 없는 금융회사 — 순이익 기준으로 행 생성")
flows = {k: {} for k in D.FLOW_KEYS}; inst = {k: {} for k in ("eq", "ca", "cl", "ltd")}
for y in (2023, 2024):
    for reprt in D.REPORTS:
        d, rc = synth(y, reprt)
        rows = [r for r in d["list"] if r["account_nm"] != "매출액" and r["sj_div"] != "CF"]
        D.ingest(rows, f"{rc[:4]}-{rc[4:6]}-{rc[6:8]}", flows, inst)
q = {k: D.quarterly(flows[k]) for k in D.FLOW_KEYS}
fin = D.assemble(q, inst, {"2023-12-31": (1000.0, "2024-03-15"), "2024-12-31": (1000.0, "2025-03-15")})
check("매출 없이도 행 생성", len(fin) > 0, True)
check("매출 결측", fin[-1]["revTtm"], None)
check("순이익 TTM", fin[-1]["niTtm"], 60.0)
check("주식수", fin[-1]["sh"], 1000.0)

print("\n6-1) 현대차식 표기 — 3개월 금액에 누적 기간 라벨, 정정본 접수일")
flows = {k: {} for k in D.FLOW_KEYS}; inst = {k: {} for k in ("eq", "ca", "cl", "ltd")}
for y in (2023, 2024):
    for reprt in D.REPORTS:
        d, rc = synth(y, reprt)
        for r in d["list"]:
            if r["sj_div"] == "IS" and reprt != "11011":
                r["thstrm_dt"] = f"{y}.01.01 ~ {y}.{Q[reprt][1].replace('-', '.')}"   # 누적 기간으로 표기
                r.pop("thstrm_add_amount", None)
        D.ingest([r for r in d["list"] if r["sj_div"] != "CF"], D.filed_of(rc, y, reprt), flows, inst)
q = D.quarterly(flows["rev"])
check("누적 라벨이어도 분기 매출 100", q.get("2024-06-30", (None, None))[1], 100.0)
check("종료일로 정한 시작일", q.get("2024-06-30", (None,))[0], "2024-04-01")
from sec_fundamentals import ttm_at
check("TTM 400", ttm_at(q, "2025-12-31")[0], 400.0)
check("정상 접수일은 그대로", D.filed_of("20240515000000", 2024, "11013"), "2024-05-15")
check("정정본(2년 뒤) 접수일은 법정 기한으로", D.filed_of("20260217000000", 2020, "11011"), "2021-03-31")
check("반기 기한 45일", D.filed_of("20260101000000", 2024, "11012"), "2024-08-14")

print("\n7) 날짜 파싱")
check("기간", D.dates_of("2025.04.01 ~ 2025.06.30"), ("2025-04-01", "2025-06-30"))
check("시점", D.dates_of("2025.06.30"), (None, "2025-06-30"))
check("빈 값", D.dates_of(""), (None, None))

print("\n" + ("실패 " + str(FAIL) if FAIL else "전부 통과"))
sys.exit(1 if FAIL else 0)
