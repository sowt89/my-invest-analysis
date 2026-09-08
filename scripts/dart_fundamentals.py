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

# 지표별 (account_id 후보, 한글 계정명 후보). 앞선 후보를 우선한다.
ACCOUNTS = {
    "rev":   (["ifrs-full_Revenue"],
              ["매출액", "수익(매출액)", "영업수익", "매출"]),
    "op":    (["dart_OperatingIncomeLoss"],
              ["영업이익", "영업이익(손실)"]),
    "ni":    (["ifrs-full_ProfitLossAttributableToOwnersOfParent", "ifrs-full_ProfitLoss"],
              ["지배기업의 소유주에게 귀속되는 당기순이익(손실)", "지배기업 소유주지분 순이익",
               "당기순이익", "당기순이익(손실)", "분기순이익", "분기순이익(손실)",
               "반기순이익", "반기순이익(손실)"]),
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
FLOW_KEYS = ("rev", "op", "ni", "cfo", "capex")


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


def pick(rows, ids, names):
    """account_id 후보 → 계정명 후보 순으로 첫 매칭 행. 계정명은 정확히 일치해야 한다."""
    by_id = {r.get("account_id"): r for r in rows}
    for i in ids:
        if i in by_id:
            return by_id[i]
    by_nm = {(r.get("account_nm") or "").replace(" ", ""): r for r in rows}
    for n in names:
        if n.replace(" ", "") in by_nm:
            return by_nm[n.replace(" ", "")]
    return None


def statements(corp, year, reprt):
    """연결 우선, 없으면 별도. (행 목록, 재무제표 구분)"""
    for fs in ("CFS", "OFS"):
        d = json.loads(get("fnlttSinglAcntAll.json", corp_code=corp, bsns_year=str(year),
                           reprt_code=reprt, fs_div=fs))
        if d.get("status") == "000" and d.get("list"):
            return d["list"], fs
    return [], None


def shares_of(corp, year, reprt):
    d = json.loads(get("stockTotqySttus.json", corp_code=corp, bsns_year=str(year),
                       reprt_code=reprt))
    for r in d.get("list") or []:
        if r.get("se", "").startswith("보통주"):
            return num(r.get("istc_totqy")), (r.get("rcept_no") or "")[:8]
    return None, None


def build(corp, this_year):
    flows = {k: {} for k in FLOW_KEYS}      # {(start,end): (val, filed)}
    inst = {k: {} for k in ("eq", "ca", "cl", "ltd")}
    shares = {}
    calls = 0
    for year in range(FIRST_YEAR, this_year + 1):
        for reprt in REPORTS:
            rows, fs = statements(corp, year, reprt)
            calls += 1
            if not rows:
                continue
            filed = f"{rows[0]['rcept_no'][:4]}-{rows[0]['rcept_no'][4:6]}-{rows[0]['rcept_no'][6:8]}"
            is_rows = [r for r in rows if r.get("sj_div") in ("IS", "CIS")]
            cf_rows = [r for r in rows if r.get("sj_div") == "CF"]
            bs_rows = [r for r in rows if r.get("sj_div") == "BS"]

            for k in ("rev", "op", "ni"):
                r = pick(is_rows, *ACCOUNTS[k])
                if not r:
                    continue
                st, en = dates_of(r.get("thstrm_dt"))
                v = num(r.get("thstrm_amount"))
                if st and en and v is not None:
                    flows[k].setdefault((st, en), (v, filed))
                va = num(r.get("thstrm_add_amount"))          # 누적 (회계연도 시작 ~ en)
                if en and va is not None:
                    flows[k].setdefault((f"{en[:4]}-01-01", en), (va, filed))
            for k in ("cfo", "capex"):
                r = pick(cf_rows, *ACCOUNTS[k])
                if not r:
                    continue
                st, en = dates_of(r.get("thstrm_dt"))
                v = num(r.get("thstrm_amount"))
                if en and v is not None:                        # 현금흐름표는 누적값
                    flows[k].setdefault((st or f"{en[:4]}-01-01", en), (abs(v), filed))
            for k in ("eq", "ca", "cl"):
                r = pick(bs_rows, *ACCOUNTS[k])
                if r:
                    _, en = dates_of(r.get("thstrm_dt"))
                    v = num(r.get("thstrm_amount"))
                    if en and v is not None:
                        inst[k].setdefault(en, (v, filed))
            debt = [num(r.get("thstrm_amount")) for r in bs_rows
                    if r.get("account_id") in DEBT[0]
                    or (r.get("account_nm") or "").replace(" ", "") in DEBT[1]]
            debt = [d for d in debt if d is not None]
            if debt and bs_rows:
                _, en = dates_of(bs_rows[0].get("thstrm_dt"))
                if en:
                    inst["ltd"].setdefault(en, (sum(debt), filed))

            sh, sh_filed = shares_of(corp, year, reprt)
            calls += 1
            if sh and bs_rows:
                _, en = dates_of(bs_rows[0].get("thstrm_dt"))
                if en:
                    shares[en] = (sh, f"{sh_filed[:4]}-{sh_filed[4:6]}-{sh_filed[6:8]}" if sh_filed else filed)
            time.sleep(0.05)
    q = {k: quarterly(flows[k]) for k in FLOW_KEYS}
    return assemble(q, inst, shares), calls


def main():
    if not KEY:
        raise SystemExit("DART_API_KEY 없음")
    cm = corp_map()
    this_year = date.today().year
    out, fails, total_calls = {}, [], 0
    for t, name, theme in WATCHLIST_KR:
        if theme == "지수 ETF":
            continue
        corp = cm.get(t.split(".")[0])
        if not corp:
            fails.append(f"{name}(고유번호 없음)")
            continue
        try:
            rows, calls = build(corp, this_year)
        except Exception as e:
            fails.append(f"{name}({type(e).__name__})")
            continue
        total_calls += calls
        out[t] = rows
        cov = lambda k: sum(1 for r in rows if r.get(k) is not None)
        print(f"  {name:10s} {len(rows):3d}분기  {rows[0]['filed'] if rows else '-'}~"
              f"{rows[-1]['filed'] if rows else '-'}  ni {cov('niTtm')} eq {cov('eq')} "
              f"ltd {cov('ltd')} fcf {cov('fcfTtm')} sh {cov('sh')}")

    n = sum(len(v) for v in out.values())
    if n < 500:
        raise SystemExit(f"레코드 {n}개 — 정상 범위(500+) 미달이라 저장하지 않는다. 실패: {fails}")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"\n{len(out)}종목 · 분기 레코드 {n}개 · API 호출 {total_calls}회 · "
          f"{os.path.getsize(OUT)//1024}KB" + (f" · 실패 {fails}" if fails else ""))


if __name__ == "__main__":
    main()
