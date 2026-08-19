#!/usr/bin/env python3
"""특정 종목의 XBRL 태그 가용성 진단 (일회성 조사용).

사용: python3 scripts/sec_tag_report.py      # TARGETS 목록 대상
개발 환경에서는 SEC 접속이 막혀 있어 Actions에서 실행한다.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sec_fundamentals import get, days

TARGETS = ["AVGO", "CEG", "BRK-B"]
LOOK = {
    "순이익": ["NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic",
             "ProfitLoss", "NetIncomeLossAttributableToParent",
             "IncomeLossFromContinuingOperations"],
    "매출": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
           "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax",
           "RegulatedAndUnregulatedOperatingRevenue"],
    "주식수": ["WeightedAverageNumberOfDilutedSharesOutstanding",
            "WeightedAverageNumberOfSharesOutstandingBasic",
            "CommonStockSharesOutstanding", "CommonStockSharesIssued"],
}


def main():
    cmap = get("https://www.sec.gov/files/company_tickers.json")
    cik = {v["ticker"]: v["cik_str"] for v in cmap.values()}
    for t in TARGETS:
        facts = get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik[t]:010d}.json")
        us = facts.get("facts", {}).get("us-gaap", {})
        print(f"\n=== {t} ===")
        for label, tags in LOOK.items():
            for tag in tags:
                units = us.get(tag, {}).get("units", {})
                for unit, recs in units.items():
                    qs = [r for r in recs if r.get("start") and r.get("end")
                          and 80 <= days(r["start"], r["end"]) <= 100]
                    if not recs:
                        continue
                    last = max(r["end"] for r in recs)
                    print(f"  {label:5s} {tag[:52]:52s} {unit:7s} "
                          f"총 {len(recs):4d} 분기 {len(qs):4d} 최신 {last}"
                          + (f" 값 {max(qs, key=lambda r: r['end'])['val']:,.0f}" if qs else ""))


if __name__ == "__main__":
    main()
