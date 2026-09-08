#!/usr/bin/env python3
"""DART(금감원 전자공시) → 한국 point-in-time 재무 데이터셋 (data/dart_pit.json).

미국 SEC 파이프라인(sec_fundamentals.py)과 같은 출력 형식·같은 분기 복원 로직을
쓴다. 그래서 fetch_data.py의 PER·PSR 실측 계산이 한국에도 그대로 적용된다.

DART와 SEC의 차이
  · 계정을 XBRL 태그 대신 account_id(ifrs-full_Revenue 등)로 맞추고,
    없으면 한글 계정명으로 대체한다. 회사마다 계정명이 다르다.
  · 손익계산서는 당기 3개월(thstrm_amount)과 누적(thstrm_add_amount)을 함께 준다.
    현금흐름표는 누적만 준다. 둘 다 quarterly()가 처리한다.
  · 사업보고서는 연간값만 있어 4분기 = 연간 − 3분기 누적으로 역산한다.
  · 조회 가능 기간은 2015년부터다.
  · 정정공시는 API가 최신본만 주므로 최초 제출값을 보장하지 못한다 (SEC와 다름).
    접수일도 정정본 날짜가 오므로(현대차는 2015~2020년 사업보고서가 모두 2022-02-17),
    법정 제출기한(분기·반기 45일, 사업보고서 90일)을 넘는 접수일은 기한으로 당긴다.
  · 손익 당기금액(thstrm_amount)은 분기·반기 보고서에서 항상 3개월치다. 그런데
    기간 표기(thstrm_dt)는 회사에 따라 누적 기간으로 적혀 있어(현대차 "01.01 ~ 06.30"에
    2분기 3개월 금액), 기간 표기 대신 종료일로 분기 시작일을 정한다.

속도: DART는 호출당 수 초가 걸린다. 회사별·보고서별로 전체 재무제표를 부르면
42종목 x 11년 x 4보고서로 수천 건이 되어 60분 제한을 넘긴다(실제로 취소됐다).
그래서 여러 회사를 한 번에 주는 주요계정 API(fnlttMultiAcnt)로 손익·재무상태를
받고, 주식수만 회사·연도별로 부른다. 현금흐름(FCF)은 주요계정에 없어 제외한다.

주요계정에 매출·순이익 행이 빠진 보고서(현대차 순이익, 카카오 매출, 금융 3사 등)만
전체 재무제표(fnlttSinglAcntAll)를 회사·보고서별로 따로 불러 메운다.
주요계정은 account_id를 주지 않아('-') 실제 매칭은 한글 계정명으로 이뤄진다.

키는 환경변수 DART_API_KEY로만 받는다. 개발 환경에서는 DART 접속이 막혀 있어
Actions에서 실행한다.
"""

import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_data import WATCHLIST_KR
from sec_fundamentals import quarterly, assemble

KEY = os.environ.get("DART_API_KEY", "")
BASE = "https://opendart.fss.or.kr/api/"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "data", "dart_pit.json")
FIRST_YEAR = 2015
REPORTS = ["11013", "11012", "11014", "11011"]          # 1분기 · 반기 · 3분기 · 사업보고서
# 보고서별 당기 3개월 기간 (전체 재무제표 응답에는 기간 필드가 없어 이걸로 채운다)
PERIOD = {"11013": ("01-01", "03-31"), "11012": ("04-01", "06-30"),
          "11014": ("07-01", "09-30"), "11011": ("01-01", "12-31")}

# 지표별 (account_id 후보, 한글 계정명 후보). 앞선 후보를 우선한다.
ACCOUNTS = {
    "rev":   (["ifrs-full_Revenue"],
              ["매출액", "수익(매출액)", "영업수익", "매출"]),
    "op":    (["dart_OperatingIncomeLoss"],
              ["영업이익", "영업이익(손실)"]),
    "ni":    (["ifrs-full_ProfitLossAttributableToOwnersOfParent", "ifrs-full_ProfitLoss"],
              ["지배기업의 소유주에게 귀속되는 당기순이익(손실)", "지배기업 소유주지분 순이익",
               "당기순이익", "당기순이익(손실)", "분기순이익", "분기순이익(손실)",
               "반기순이익", "반기순이익(손실)",
               "연결당기순이익", "연결분기순이익", "연결반기순이익"]),   # 현대차식 표기
    "cfo":   (["ifrs-full_CashFlowsFromUsedInOperatingActivities"],
              ["영업활동현금흐름", "영업활동으로 인한 현금흐름", "영업활동으로부터의 현금흐름"]),
    "capex": (["ifrs-full_PurchaseOfPropertyPlantAndEquipment"],
              ["유형자산의 취득", "유형자산의 증가", "유형자산 취득"]),
    "eq":    (["ifrs-full_EquityAttributableToOwnersOfParent", "ifrs-full_Equity"],
              ["지배기업의 소유주에게 귀속되는 자본", "지배기업 소유주지분", "자본총계"]),
    "ca":    (["ifrs-full_CurrentAssets"], ["유동자산"]),
    "cl":    (["ifrs-full_CurrentLiabilities"], ["유동부채"]),
}
DEBT = (["ifrs-full_LongtermBorrowings", "ifrs-full_BondsIssued"], ["장기차입금", "사채"])
FLOW_KEYS = ("rev", "op", "ni", "cfo", "capex")   # cfo·capex는 주요계정에 없어 비어 있다


def get(endpoint, **params):
    params["crtfc_key"] = KEY
    url = BASE + endpoint + "?" + urllib.parse.urlencode(params)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return r.read()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2)


def corp_map():
    z = zipfile.ZipFile(io.BytesIO(get("corpCode.xml")))
    root = ET.fromstring(z.read(z.namelist()[0]))
    return {(el.findtext("stock_code") or "").strip(): el.findtext("corp_code")
            for el in root.iter("list") if (el.findtext("stock_code") or "").strip()}


def num(s):
    s = (s or "").replace(",", "").strip()
    if not s or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def dates_of(s):
    """'2025.01.01 ~ 2025.03.31' → ('2025-01-01','2025-03-31'), '2025.03.31' → (None, ...)"""
    ds = re.findall(r"(\d{4})\.(\d{2})\.(\d{2})", s or "")
    ds = [f"{y}-{m}-{d}" for y, m, d in ds]
    if len(ds) >= 2:
        return ds[0], ds[1]
    return (None, ds[0]) if ds else (None, None)


def norm_id(i):
    """'ifrs-full_Revenue'·'ifrs_Revenue'(2019년 이전 표기) → 'Revenue'. 표준코드 없으면 None."""
    i = i or ""
    return i.split("_", 1)[1] if "_" in i and not i.startswith("-") else None


def qstart(en):
    """분기 종료일 → 그 3개월 구간 시작일. 12-31은 연간(사업보고서)이라 01-01."""
    m = int(en[5:7])
    return f"{en[:4]}-01-01" if m == 12 else f"{en[:4]}-{m - 2:02d}-01"


DEADLINE = {"11013": 45, "11012": 45, "11014": 45, "11011": 90}     # 법정 제출기한 (일)


def filed_of(rcept_no, year, reprt):
    """접수번호 앞 8자리 → 제출일. 정정본이라 기한을 넘긴 날짜면 법정 기한으로 당긴다."""
    rc = rcept_no or ""
    if len(rc) < 8:
        return None
    filed = date(int(rc[:4]), int(rc[4:6]), int(rc[6:8]))
    end = date(int(year), *map(int, PERIOD[reprt][1].split("-")))
    limit = date.fromordinal(end.toordinal() + DEADLINE[reprt])
    return min(filed, limit).isoformat()


def pick(rows, ids, names):
    """account_id 후보 → 계정명 후보 순으로 첫 매칭 행. 계정명은 정확히 일치해야 한다.

    같은 계정이 손익계산서(IS)와 포괄손익계산서(CIS)에 겹쳐 나오면 앞선 행(IS)을 쓴다.
    """
    by_id, by_nm = {}, {}
    for r in rows:
        by_id.setdefault(norm_id(r.get("account_id")), r)
        by_nm.setdefault((r.get("account_nm") or "").replace(" ", ""), r)
    for i in ids:
        if norm_id(i) in by_id:
            return by_id[norm_id(i)]
    for n in names:
        if n.replace(" ", "") in by_nm:
            return by_nm[n.replace(" ", "")]
    return None


def major_accounts(corps, year, reprt):
    """주요계정 일괄 조회. {corp_code: (행 목록, fs_div)} — 연결 우선, 없으면 별도."""
    out = {}
    for i in range(0, len(corps), 20):                     # 한 번에 20개씩
        d = json.loads(get("fnlttMultiAcnt.json", corp_code=",".join(corps[i:i + 20]),
                           bsns_year=str(year), reprt_code=reprt))
        if d.get("status") != "000":
            continue
        for r in d.get("list") or []:
            out.setdefault(r["corp_code"], {}).setdefault(r.get("fs_div"), []).append(r)
    return {c: (v.get("CFS") or v.get("OFS") or [], "CFS" if v.get("CFS") else "OFS")
            for c, v in out.items()}


def full_statement(corp, year, reprt):
    """전체 재무제표(회사·보고서별 1건). 연결 우선, 없으면 별도. 없으면 []."""
    for fs in ("CFS", "OFS"):
        d = json.loads(get("fnlttSinglAcntAll.json", corp_code=corp, bsns_year=str(year),
                           reprt_code=reprt, fs_div=fs))
        if d.get("status") == "000" and d.get("list"):
            return d["list"]
    return []


def lacks(rows, keys=("rev", "ni")):
    """손익 행에서 keys 중 하나라도 못 찾으면 True (전체 재무제표 보강 대상)."""
    is_rows = [r for r in rows if r.get("sj_div") in ("IS", "CIS")]
    return any(pick(is_rows, *ACCOUNTS[k]) is None for k in keys)


def shares_of(corp, year):
    """사업보고서 기준 보통주 발행주식수. (값, 접수일)"""
    d = json.loads(get("stockTotqySttus.json", corp_code=corp, bsns_year=str(year),
                       reprt_code="11011"))
    for r in d.get("list") or []:
        if "보통주" in (r.get("se") or ""):
            rc = (r.get("rcept_no") or "")[:8]
            return num(r.get("istc_totqy")), (f"{rc[:4]}-{rc[4:6]}-{rc[6:8]}" if len(rc) == 8 else None)
    return None, None


def ingest(rows, filed, flows, inst, period=None):
    """한 보고서의 계정 행을 분기값·잔액 사전에 넣는다. 먼저 넣은 값을 유지한다.

    period: 기간 필드(thstrm_dt)가 없는 전체 재무제표 행에 쓸 (시작일, 종료일).
    """
    is_rows = [r for r in rows if r.get("sj_div") in ("IS", "CIS")]
    bs_rows = [r for r in rows if r.get("sj_div") == "BS"]
    for k in ("rev", "op", "ni"):
        r = pick(is_rows, *ACCOUNTS[k])
        if not r:
            continue
        _, en = dates_of(r.get("thstrm_dt"))
        en = en or (period and period[1])
        if not en:
            continue
        st = qstart(en)                                    # 당기금액은 항상 3개월(연간은 12개월)
        v = num(r.get("thstrm_amount"))
        if v is not None:
            flows[k].setdefault((st, en), (v, filed))
        va = num(r.get("thstrm_add_amount"))               # 누적 (회계연도 시작 ~ en)
        if en and va is not None:
            flows[k].setdefault((f"{en[:4]}-01-01", en), (va, filed))
    for k in ("eq", "ca", "cl"):
        r = pick(bs_rows, *ACCOUNTS[k])
        if r:
            _, en = dates_of(r.get("thstrm_dt"))
            en = en or (period and period[1])
            v = num(r.get("thstrm_amount"))
            if en and v is not None:
                inst[k].setdefault(en, (v, filed))
    debt = [num(r.get("thstrm_amount")) for r in bs_rows
            if r.get("account_id") in DEBT[0]
            or (r.get("account_nm") or "").replace(" ", "") in DEBT[1]]
    debt = [d for d in debt if d is not None]
    if debt and bs_rows:
        _, en = dates_of(bs_rows[0].get("thstrm_dt"))
        en = en or (period and period[1])
        if en:
            inst["ltd"].setdefault(en, (sum(debt), filed))


def build_all(corps, this_year):
    """{corp_code: rows}. 손익·재무상태는 일괄, 주식수는 회사·연도별."""
    flows = {c: {k: {} for k in FLOW_KEYS} for c in corps}
    inst = {c: {k: {} for k in ("eq", "ca", "cl", "ltd")} for c in corps}
    shares = {c: {} for c in corps}
    calls, needs = 0, []                        # needs: 매출·순이익이 빠진 (회사, 연도, 보고서)
    for year in range(FIRST_YEAR, this_year + 1):
        for reprt in REPORTS:
            got = major_accounts(corps, year, reprt)
            calls += (len(corps) + 19) // 20
            for c in corps:
                rows, _ = got.get(c, ([], None))
                if rows:
                    ingest(rows, filed_of(rows[0].get("rcept_no"), year, reprt), flows[c], inst[c])
                if lacks(rows):
                    needs.append((c, year, reprt))
        print(f"  {year} 재무 수집 완료", flush=True)
    from concurrent.futures import ThreadPoolExecutor
    # 주요계정에 빠진 보고서만 전체 재무제표로 보강 (회사·보고서별 호출)
    def fill(job):
        c, y, reprt = job
        try:
            return job, full_statement(c, y, reprt)
        except Exception:
            return job, []
    filled = 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        for (c, y, reprt), rows in ex.map(fill, needs):
            if not rows:
                continue
            st, en = PERIOD[reprt]
            ingest(rows, filed_of(rows[0].get("rcept_no"), y, reprt), flows[c], inst[c],
                   period=(f"{y}-{st}", f"{y}-{en}"))
            filled += 1
    calls += len(needs) * 2                     # CFS·OFS 최대 2회로 잡는다
    print(f"  전체 재무제표 보강 {filled}/{len(needs)}건", flush=True)
    # 주식수: 회사 x 연도 (사업보고서). 결산일을 기준일로 쓴다.
    jobs = [(c, y) for c in corps for y in range(FIRST_YEAR, this_year + 1)]
    def one(job):
        c, y = job
        try:
            return c, y, shares_of(c, y)
        except Exception:
            return c, y, (None, None)
    with ThreadPoolExecutor(max_workers=4) as ex:
        for c, y, (sh, filed) in ex.map(one, jobs):
            if sh and filed:
                shares[c][f"{y}-12-31"] = (sh, filed)
    calls += len(jobs)
    print(f"  주식수 수집 완료 ({len(jobs)}건)", flush=True)
    out = {}
    for c in corps:
        q = {k: quarterly(flows[c][k]) for k in FLOW_KEYS}
        out[c] = assemble(q, inst[c], shares[c])
    return out, calls


def main():
    if not KEY:
        raise SystemExit("DART_API_KEY 없음")
    cm = corp_map()
    this_year = date.today().year
    targets = [(t, n, cm.get(t.split(".")[0])) for t, n, th in WATCHLIST_KR if th != "지수 ETF"]
    fails = [f"{n}(고유번호 없음)" for t, n, c in targets if not c]
    targets = [(t, n, c) for t, n, c in targets if c]
    print(f"대상 {len(targets)}종목 · {FIRST_YEAR}~{this_year}", flush=True)

    built, calls = build_all([c for _, _, c in targets], this_year)
    out = {}
    for t, name, c in targets:
        rows = built.get(c, [])
        out[t] = rows
        cov = lambda k: sum(1 for r in rows if r.get(k) is not None)
        print(f"  {name:10s} {len(rows):3d}분기  {rows[0]['filed'] if rows else '-'}~"
              f"{rows[-1]['filed'] if rows else '-'}  ni {cov('niTtm')} eq {cov('eq')} "
              f"ltd {cov('ltd')} sh {cov('sh')}", flush=True)

    n = sum(len(v) for v in out.values())
    if n < 500:
        raise SystemExit(f"레코드 {n}개 — 정상 범위(500+) 미달이라 저장하지 않는다. 실패: {fails}")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"\n{len(out)}종목 · 분기 레코드 {n}개 · API 호출 {calls}회 · "
          f"{os.path.getsize(OUT)//1024}KB" + (f" · 실패 {fails}" if fails else ""), flush=True)


if __name__ == "__main__":
    main()
