#!/usr/bin/env python3
"""SEC XBRL 가용성 탐침 — 전체 파이프라인을 짜기 전에 무엇을 얻을 수 있는지 확인한다.

확인 항목:
  1. 워치리스트 40종목의 CIK 매핑 성공률
  2. 재무 점수 5개 지표에 필요한 us-gaap 태그의 종목별 존재율
  3. as-reported 시계열의 시작 연도 (얼마나 과거까지 백테스트 가능한가)
  4. 제출일(filed) 기준 point-in-time 구성 가능 여부
"""

import json
import os
import sys
import time
import urllib.request
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_data import WATCHLIST

UA = {"User-Agent": "my-invest-analysis research sowt89@gmail.com"}

# 재무 점수 5개 지표에 필요한 항목 → 후보 태그 (회사마다 쓰는 태그가 다르다)
NEEDED = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "operatingIncome": ["OperatingIncomeLoss"],
    "netIncome": ["NetIncomeLoss"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "assetsCurrent": ["AssetsCurrent"],
    "liabilitiesCurrent": ["LiabilitiesCurrent"],
    "longTermDebt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment",
              "PaymentsToAcquireProductiveAssets"],
}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main():
    tickers = [t for t, _, theme in WATCHLIST if theme != "지수 ETF"]
    print(f"대상 {len(tickers)}종목\n")

    cmap = get("https://www.sec.gov/files/company_tickers.json")
    cik = {v["ticker"]: v["cik_str"] for v in cmap.values()}
    missing = [t for t in tickers if t not in cik]
    print(f"CIK 매핑: {len(tickers) - len(missing)}/{len(tickers)} 성공"
          + (f" · 실패 {missing}" if missing else ""))

    have = defaultdict(int)          # 항목별 보유 종목 수
    first_year, filed_ok, fails = [], 0, []
    for t in tickers:
        if t not in cik:
            continue
        try:
            facts = get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik[t]:010d}.json")
        except Exception as e:
            fails.append((t, str(e)[:60]))
            continue
        us = facts.get("facts", {}).get("us-gaap", {})
        oldest = None
        for item, cands in NEEDED.items():
            tag = next((c for c in cands if c in us), None)
            if not tag:
                continue
            have[item] += 1
            for unit in us[tag]["units"].values():
                for rec in unit:
                    if rec.get("filed"):
                        oldest = min(oldest or rec["end"], rec["end"])
        if oldest:
            first_year.append(int(oldest[:4]))
        # point-in-time 확인: 같은 분기 값이 나중 제출에서 수정되는지
        oi = us.get("OperatingIncomeLoss", {}).get("units", {}).get("USD", [])
        if any(r.get("filed") and r.get("start") and r.get("end") for r in oi):
            filed_ok += 1
        time.sleep(0.12)             # SEC 권장 10 req/s 이하

    n = len(tickers) - len(missing) - len(fails)
    print(f"companyfacts 수집: {n}종목 성공" + (f" · 실패 {fails}" if fails else ""))
    print("\n항목별 보유 종목 수:")
    for item in NEEDED:
        print(f"  {item:16s} {have[item]:3d}/{n}")
    print(f"\nfiled(제출일) 포함 종목: {filed_ok}/{n}")
    if first_year:
        print("데이터 시작 연도 분포:", dict(sorted(Counter(first_year).items())))
        print(f"→ 대부분 {sorted(first_year)[len(first_year)//2]}년부터 백테스트 가능")


if __name__ == "__main__":
    main()
