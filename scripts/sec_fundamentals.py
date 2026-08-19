#!/usr/bin/env python3
"""SEC XBRL → point-in-time 재무 데이터셋 (data/sec_pit.json).

목적: 재무·실적 점수의 예측력을 실제로 검증하기 위한 as-reported 시계열 구축.

핵심 원칙
  1. 같은 기간이 여러 번 제출되면 **최초 제출값**만 쓴다 (수정 후 값 배제).
  2. 각 레코드는 제출일(filed)을 갖는다 — 그 날짜 이후에만 사용해야 미래 정보가 새지 않는다.
  3. 분기 흐름값은 TTM(최근 4분기 합)으로 집계한다.

출력: {ticker: [{filed, end, revTtm, opTtm, niTtm, fcfTtm, eq, ca, cl, ltd,
                revTtmPrev, revTtm3y}, ...]}  (filed 오름차순)
"""

import json
import os
import sys
import time
import urllib.request
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_data import WATCHLIST

UA = {"User-Agent": "my-invest-analysis research sowt89@gmail.com"}
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "data", "sec_pit.json")

FLOW = {   # 기간 흐름값
    "rev": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
            "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "op": ["OperatingIncomeLoss"],
    "ni": ["NetIncomeLoss"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
}
SHARES = {   # 발행주식수 (dei 우선, 없으면 us-gaap)
    "dei": ["EntityCommonStockSharesOutstanding"],
    "us-gaap": ["CommonStockSharesOutstanding",
                "WeightedAverageNumberOfDilutedSharesOutstanding"],
}
INSTANT = {  # 시점 잔액
    "eq": ["StockholdersEquity",
           "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "ca": ["AssetsCurrent"],
    "cl": ["LiabilitiesCurrent"],
    "ltd": ["LongTermDebtNoncurrent", "LongTermDebt"],
}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


def days(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def collect(us, cands, instant, unit_name="USD"):
    """{기간키: (값, 최초제출일)}. 최초 제출값만 남긴다."""
    out = {}
    for tag in cands:                       # 앞선 태그를 우선하되, 빈 구간은 뒤 태그로 보완
        for unit, recs in us.get(tag, {}).get("units", {}).items():
            if unit != unit_name:
                continue
            for r in recs:
                f, e = r.get("filed"), r.get("end")
                if not f or not e or r.get("val") is None:
                    continue
                key = e if instant else (r.get("start"), e)
                if not instant and not r.get("start"):
                    continue
                cur = out.get(key)
                if cur is None or f < cur[1]:
                    out[key] = (float(r["val"]), f)
    return out


def quarterly(flows):
    """분기값을 만든다. {분기종료일: (시작일, 값, 제출일)}

    분기 종료일을 키로 삼아 같은 분기가 중복 집계되지 않게 한다.
    원래 분기값을 우선하고, 없으면 누적(YTD) 차분 → 연간-3분기 순으로 채운다.
    현금흐름표 등은 분기가 아니라 회계연도 누적으로 제출되기 때문이다.
    """
    q = {}
    for (st, e), (val, filed) in flows.items():          # 1) 원래 분기값
        if 80 <= days(st, e) <= 100:
            q[e] = (st, val, filed)

    by_start = {}                                        # 2) 누적값의 인접 차분
    for (st, e), (val, filed) in flows.items():
        by_start.setdefault(st, []).append((e, val, filed))
    for st, lst in by_start.items():
        lst.sort()
        for (e0, v0, f0), (e1, v1, f1) in zip(lst, lst[1:]):
            if 80 <= days(e0, e1) <= 100 and e1 not in q:
                q[e1] = (e0, v1 - v0, max(f0, f1))

    for (st, e), (val, filed) in flows.items():          # 3) 연간 - 3분기 = Q4
        if not 350 <= days(st, e) <= 380 or e in q:
            continue
        parts = [qe for qe, (qs, _, _) in q.items() if st <= qs and qe < e]
        if len(parts) == 3:
            got = sum(q[qe][1] for qe in parts)
            fl = max(q[qe][2] for qe in parts)
            q[e] = (max(parts), val - got, max(filed, fl))
    return q


def ttm_at(q, cutoff, end=None):
    """cutoff 시점까지 제출된 분기값으로 TTM. end 지정 시 그 분기 종료일 기준."""
    avail = sorted(e for e, (_, _, f) in q.items()
                   if f <= cutoff and (end is None or e <= end))
    if len(avail) < 4:
        return None, None
    last4 = avail[-4:]
    if days(q[last4[0]][0], last4[-1]) > 400:            # 구멍 있는 4분기는 버린다
        return None, None
    return sum(q[e][1] for e in last4), last4[-1]


def build(ticker, cik):
    facts = get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
    us = facts.get("facts", {}).get("us-gaap", {})
    flows = {k: quarterly(collect(us, c, False)) for k, c in FLOW.items()}
    inst = {k: collect(us, c, True) for k, c in INSTANT.items()}
    shares = collect(facts.get("facts", {}).get("dei", {}), SHARES["dei"], True, "shares")
    if not shares:
        shares = collect(us, SHARES["us-gaap"], True, "shares")

    filings = sorted({f for d in list(flows.values()) + list(inst.values())
                      for (_, f) in d.values()})
    rows, seen = [], set()
    for f in filings:
        rev, rend = ttm_at(flows["rev"], f)
        if rev is None or rend in seen:
            continue
        seen.add(rend)
        row = {"filed": f, "end": rend, "revTtm": rev}
        for k in ("op", "ni", "cfo", "capex"):
            row[k + "Ttm"] = ttm_at(flows[k], f, rend)[0]
        for k, d in inst.items():
            picks = [(e, v) for e, (v, ff) in d.items() if ff <= f and e <= rend]
            row[k] = max(picks)[1] if picks else None
        # 발행주식수는 표지에 기재돼 결산일보다 늦을 수 있으므로 제출일 기준으로만 자른다
        sp = [(e, v) for e, (v, ff) in shares.items() if ff <= f]
        row["sh"] = max(sp)[1] if sp else None
        # 성장률용 과거 TTM (같은 시점에 이미 제출돼 있던 값만)
        for label, back in (("revTtmPrev", 340), ("revTtm3y", 1080)):
            past = (date.fromisoformat(rend).toordinal() - back)
            pe = date.fromordinal(past).isoformat()
            row[label] = ttm_at(flows["rev"], f, pe)[0]
        if row["cfoTtm"] is not None and row["capexTtm"] is not None:
            row["fcfTtm"] = row["cfoTtm"] - row["capexTtm"]
        else:
            row["fcfTtm"] = None
        rows.append(row)
    return rows


def main():
    tickers = [t for t, _, theme in WATCHLIST if theme != "지수 ETF"]
    cmap = get("https://www.sec.gov/files/company_tickers.json")
    cik = {v["ticker"]: v["cik_str"] for v in cmap.values()}

    out, fails = {}, []
    for t in tickers:
        if t not in cik:
            fails.append(t)
            continue
        try:
            rows = build(t, cik[t])
        except Exception as e:
            fails.append(f"{t}({type(e).__name__})")
            continue
        out[t] = rows
        cov = lambda k: sum(1 for r in rows if r.get(k) is not None)
        print(f"  {t:6s} {len(rows):3d}분기  {rows[0]['filed'] if rows else '-'}~"
              f"{rows[-1]['filed'] if rows else '-'}  "
              f"op {cov('opTtm')} eq {cov('eq')} ltd {cov('ltd')} fcf {cov('fcfTtm')} "
              f"sh {cov('sh')}")
        time.sleep(0.12)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    n = sum(len(v) for v in out.values())
    print(f"\n{len(out)}종목 · 분기 레코드 {n}개 · {os.path.getsize(OUT)//1024}KB"
          + (f" · 실패 {fails}" if fails else ""))
    early = sorted(r["filed"] for v in out.values() for r in v)
    if early:
        print(f"제출일 범위 {early[0]} ~ {early[-1]}")


if __name__ == "__main__":
    main()
