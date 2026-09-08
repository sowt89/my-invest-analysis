#!/usr/bin/env python3
"""DART(금감원 전자공시) API 가용성 탐침 — 본 파이프라인을 짜기 전에 확인한다.

키는 환경변수 DART_API_KEY로만 받고 절대 출력하지 않는다.
개발 환경에서는 opendart.fss.or.kr 접속이 막혀 있어 Actions에서 실행한다.

확인 항목
  1. 워치리스트 종목코드 → DART 고유번호(corp_code) 매핑 성공률
  2. 연결재무제표에서 매출·영업이익·순이익·자본·유동자산/부채·차입금 계정명
  3. 발행주식총수 API
  4. 가장 오래된 조회 가능 연도 (얼마나 과거까지 실측이 가능한가)
  5. 공시 접수일(rcept_no 앞 8자리) — point-in-time 구성 가능 여부
"""

import io
import json
import os
import sys
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_data import WATCHLIST_KR

KEY = os.environ.get("DART_API_KEY", "")
BASE = "https://opendart.fss.or.kr/api/"
KEYWORDS = ["매출액", "수익(매출액)", "영업수익", "영업이익", "당기순이익", "분기순이익",
            "자본총계", "유동자산", "유동부채", "비유동부채", "장기차입금", "사채",
            "영업활동현금흐름", "유형자산의 취득"]


def get(endpoint, **params):
    params["crtfc_key"] = KEY
    url = BASE + endpoint + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


def corp_map():
    """DART 고유번호 전체 목록(zip 안의 XML) → {종목코드: (corp_code, 회사명)}"""
    z = zipfile.ZipFile(io.BytesIO(get("corpCode.xml")))
    root = ET.fromstring(z.read(z.namelist()[0]))
    out = {}
    for el in root.iter("list"):
        sc = (el.findtext("stock_code") or "").strip()
        if sc:
            out[sc] = (el.findtext("corp_code"), el.findtext("corp_name"))
    return out


def main():
    if not KEY:
        raise SystemExit("DART_API_KEY 없음 — GitHub Secrets 등록을 확인하세요")
    print(f"키 감지: 길이 {len(KEY)}자 (값은 출력하지 않음)\n")

    cm = corp_map()
    print(f"DART 상장사 고유번호 {len(cm):,}개 수신")
    tickers = [(t.split(".")[0], n) for t, n, th in WATCHLIST_KR if th != "지수 ETF"]
    hit = [(sc, n, cm[sc]) for sc, n in tickers if sc in cm]
    miss = [(sc, n) for sc, n in tickers if sc not in cm]
    print(f"워치리스트 매핑: {len(hit)}/{len(tickers)}" + (f" · 실패 {miss}" if miss else ""))

    print("\n■ 연결재무제표 계정명 (2025년 1분기)")
    for sc, name, (code, _) in hit[:3]:
        d = json.loads(get("fnlttSinglAcntAll.json", corp_code=code, bsns_year="2025",
                           reprt_code="11013", fs_div="CFS"))
        rows = d.get("list") or []
        print(f"  {name}: status {d.get('status')} · 행 {len(rows)}")
        seen = set()
        for r in rows:
            nm = r.get("account_nm", "")
            if any(k in nm for k in KEYWORDS) and nm not in seen:
                seen.add(nm)
                print(f"     [{r.get('sj_div')}] {nm:28s} 당기 {r.get('thstrm_amount','')[:16]:>16s}"
                      f"  누적 {r.get('thstrm_add_amount','')[:16]:>16s}  접수 {r.get('rcept_no','')[:8]}")

    print("\n■ 발행주식총수 (삼성전자 2024 사업보고서)")
    code = cm["005930"][0]
    d = json.loads(get("stockTotqySttus.json", corp_code=code, bsns_year="2024", reprt_code="11011"))
    for r in (d.get("list") or [])[:4]:
        print(f"  {r.get('se','')}: 발행 {r.get('istc_totqy','')} · 유통 {r.get('distb_stock_co','')}")

    print("\n■ 조회 가능 연도 (삼성전자, 사업보고서 연결)")
    for y in ["2015", "2013", "2011"]:
        d = json.loads(get("fnlttSinglAcntAll.json", corp_code=code, bsns_year=y,
                           reprt_code="11011", fs_div="CFS"))
        print(f"  {y}: status {d.get('status')} · 행 {len(d.get('list') or [])}")


if __name__ == "__main__":
    main()
