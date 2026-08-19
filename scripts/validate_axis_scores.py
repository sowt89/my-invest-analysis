#!/usr/bin/env python3
"""재무·실적 점수의 예측력 검증 (point-in-time).

data/sec_pit.json의 as-reported 재무제표로, 각 시점에 **실제로 공개돼 있던**
정보만 써서 과거 점수를 재구성하고 이후 수익률과의 관계를 측정한다.

측정 지표
  IC     : 월별 횡단면 순위상관(Spearman)의 평균. 점수가 높은 종목이
           실제로 더 올랐는지. |IC| 0.02~0.05면 약한 유효성, 0.05+면 의미 있음.
  t값    : IC 평균 / (표준편차/√월수). |t| 2 이상이면 우연으로 보기 어렵다.
  상위-하위: 매월 점수 상위 1/3 평균수익 - 하위 1/3 평균수익.
  랜덤   : 같은 절차를 점수 섞어서 반복 — 대조군.

채점은 scripts/fetch_data.py의 함수를 그대로 사용한다.

주의: 부채비율은 실서비스가 yfinance의 총부채/자본을 쓰는 반면 여기서는
SEC의 장기부채/자본을 쓴다. 과거 총부채를 point-in-time으로 복원하기
어려워 생긴 차이이며, 재무 점수 5개 지표 중 1개에만 영향을 준다.
"""

import json
import math
import os
import random
import statistics as st
import sys
from datetime import date

import yfinance as yf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_data import (WATCHLIST, finance_score, growth_score,
                        valuation_score, make_session)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIT = os.path.join(_ROOT, "data", "sec_pit.json")
CACHE = os.path.join(_ROOT, "data", "px_monthly.json")
HORIZONS = (3, 6, 12)


def month_ends(start, end):
    out, y, m = [], int(start[:4]), int(start[5:7])
    while f"{y:04d}-{m:02d}" <= end[:7]:
        nm, ny = (1, y + 1) if m == 12 else (m + 1, y)
        out.append((date(ny, nm, 1).toordinal() - 1))
        y, m = ny, nm
    return [date.fromordinal(o).isoformat() for o in out]


def load_prices(tickers):
    if os.path.exists(CACHE):
        with open(CACHE) as f:
            return json.load(f)
    session = make_session()
    px = {}
    for t in tickers:
        h = yf.Ticker(t, session=session).history(period="max", interval="1mo",
                                                  auto_adjust=True)
        px[t] = {d.date().isoformat(): float(c) for d, c in zip(h.index, h["Close"])
                 if c == c}
        print(f"  {t}: {len(px[t])}개월")
    with open(CACHE, "w") as f:
        json.dump(px, f, separators=(",", ":"))
    return px


def price_at(series, ym):
    """해당 월 이하의 마지막 종가."""
    ks = [d for d in series if d[:7] <= ym]
    return series[max(ks)] if ks else None


def multiples(rows, months, series):
    """월별 PER·PSR·FCF수익률을 실측 계산한다. {월: {...}}

    각 월에 대해 그 시점까지 제출된 최신 재무만 쓴다(미래 정보 차단).
    적자 구간은 PER이 성립하지 않아 제외한다.
    """
    out, idx = {}, 0
    usable = [r for r in rows if r.get("sh")]
    if not usable:
        return out
    for ym in months:
        px = price_at(series, ym[:7])
        if px is None:
            continue
        while idx + 1 < len(usable) and usable[idx + 1]["filed"] <= ym:
            idx += 1
        r = usable[idx]
        if r["filed"] > ym:
            continue
        mcap = px * r["sh"]
        out[ym] = {
            "per": mcap / r["niTtm"] if (r.get("niTtm") or 0) > 0 else None,
            "psr": mcap / r["revTtm"] if (r.get("revTtm") or 0) > 0 else None,
            "fcfY": r["fcfTtm"] / mcap * 100 if r.get("fcfTtm") is not None else None,
        }
    return out


def value_score_at(mult, months, i):
    """밸류 점수 (-5~+6). PER 3년 괴리 · PSR 5년 괴리 · FCF 수익률.
    PEG는 컨센서스가 필요해 결측(0점) 처리한다."""
    cur = mult.get(months[i])
    if not cur:
        return None
    def gap(key, back):
        now = cur.get(key)
        hist = [mult[m][key] for m in months[max(0, i - back):i]
                if m in mult and mult[m].get(key)]
        if not now or len(hist) < back // 2:
            return None
        return (now / (sum(hist) / len(hist)) - 1) * 100
    d = {"gap": gap("per", 36), "psr": 1, "avgPsr": None,
         "fcfY": cur.get("fcfY"), "peg": None}
    pg = gap("psr", 60)
    if pg is not None:                       # valuation_score는 psr/avgPsr 비율을 본다
        d["psr"], d["avgPsr"] = 1 + pg / 100, 1
    return valuation_score(d)[0]


def scores_at(rows, asof):
    """asof 시점까지 제출된 최신 재무로 재무/실적 점수를 만든다."""
    avail = [r for r in rows if r["filed"] <= asof]
    if not avail:
        return None
    r = avail[-1]
    rev, op, eq = r.get("revTtm"), r.get("opTtm"), r.get("eq")
    d = {
        "op": op / rev * 100 if (rev and op is not None) else None,
        "roe": r["niTtm"] / eq * 100 if (r.get("niTtm") is not None and eq) else None,
        "ltDE": r["ltd"] / eq if (r.get("ltd") is not None and eq) else None,
        "curR": r["ca"] / r["cl"] if (r.get("ca") is not None and r.get("cl")) else None,
        "fcfM": r["fcfTtm"] / rev * 100 if (r.get("fcfTtm") is not None and rev) else None,
    }
    fin = finance_score(d)[0]

    prev, p3 = r.get("revTtmPrev"), r.get("revTtm3y")
    yoy = (rev / prev - 1) * 100 if (rev and prev) else None
    cagr = ((rev / p3) ** (1 / 3) - 1) * 100 if (rev and p3 and p3 > 0) else None
    # 마진 방향: 1년 전 제출분과 비교 (실적 점수의 marginTrend와 같은 취지)
    # growth_score의 marginTrend는 qRev/qOp의 [-1]과 [-5]만 본다.
    # 1년 전 제출분의 TTM을 [-5]에, 현재 TTM을 [-1]에 넣어 같은 비교를 만든다.
    old = [x for x in avail if x["filed"] <= _minus_year(asof)]
    qRev = qOp = None
    if old and old[-1].get("revTtm") and old[-1].get("opTtm") is not None \
            and rev and op is not None:
        qRev = [old[-1]["revTtm"], None, None, None, rev]
        qOp = [old[-1]["opTtm"], None, None, None, op]
    g = growth_score({"revG": [None, yoy, cagr], "surprise": None, "est": None,
                      "qRev": qRev, "qOp": qOp})[0]
    return fin, g


def _minus_year(d):
    return f"{int(d[:4]) - 1}{d[4:]}"


def spearman(pairs):
    n = len(pairs)
    if n < 5:
        return None
    def ranks(vals):
        order = sorted(range(n), key=lambda i: vals[i])
        rk = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                rk[order[k]] = avg
            i = j + 1
        return rk
    a, b = ranks([p[0] for p in pairs]), ranks([p[1] for p in pairs])
    ma, mb = st.mean(a), st.mean(b)
    va = math.sqrt(sum((x - ma) ** 2 for x in a))
    vb = math.sqrt(sum((x - mb) ** 2 for x in b))
    if va == 0 or vb == 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (va * vb)


def summarize(label, ics, spreads):
    ics = [x for x in ics if x is not None]
    if len(ics) < 12:
        print(f"  {label:22s} 표본 부족 ({len(ics)}개월)")
        return
    m, sd = st.mean(ics), st.pstdev(ics)
    t = m / (sd / math.sqrt(len(ics))) if sd else 0
    sp = [x for x in spreads if x is not None]
    print(f"  {label:22s} IC {m:+.3f}  t {t:+.2f}  상위-하위 {st.mean(sp):+.2f}%p  "
          f"({len(ics)}개월)")


def run(tickers, px, months, rows, label):
    print(f"\n{'='*62}\n{label} · {len(tickers)}종목\n{'='*62}")
    for hz in HORIZONS:
        print(f"■ {hz}개월 선행수익률")
        for idx, name in ((0, "재무 점수"), (1, "실적 점수(3/5지표)"),
                          (2, "밸류 점수(3/4지표)")):
            for era, lo, hi in (("전체", "2010", "2027"), ("2010~2015", "2010", "2016"),
                                ("2016~2020", "2016", "2021"), ("2021~2026", "2021", "2027")):
                ics, spreads = [], []
                for i, ym in enumerate(months):
                    if not (lo <= ym[:4] < hi) or i + hz >= len(months):
                        continue
                    pairs = []
                    for t in tickers:
                        s = rows.get((ym, t))
                        p0 = price_at(px.get(t, {}), ym[:7])
                        p1 = price_at(px.get(t, {}), months[i + hz][:7])
                        if s and p0 and p1 and s[idx] is not None:
                            pairs.append((s[idx], (p1 / p0 - 1) * 100))
                    if len(pairs) < 10:
                        continue
                    ics.append(spearman(pairs))
                    srt = sorted(pairs)
                    k = max(1, len(srt) // 3)
                    spreads.append(st.mean(r for _, r in srt[-k:])
                                   - st.mean(r for _, r in srt[:k]))
                summarize(f"{name} · {era}", ics, spreads)
        # 랜덤 대조군
        random.seed(42)
        ics, spreads = [], []
        for i, ym in enumerate(months):
            if i + hz >= len(months):
                continue
            pairs = []
            for t in tickers:
                p0 = price_at(px.get(t, {}), ym[:7])
                p1 = price_at(px.get(t, {}), months[i + hz][:7])
                if (ym, t) in rows and p0 and p1:
                    pairs.append((random.random(), (p1 / p0 - 1) * 100))
            if len(pairs) < 10:
                continue
            ics.append(spearman(pairs))
            srt = sorted(pairs)
            k = max(1, len(srt) // 3)
            spreads.append(st.mean(r for _, r in srt[-k:]) - st.mean(r for _, r in srt[:k]))
        summarize("랜덤 대조군", ics, spreads)
        print()


def main():
    with open(PIT) as f:
        pit = json.load(f)
    tickers = [t for t in pit if pit[t]]
    px = load_prices(tickers)
    months = month_ends("2010-06-30", "2026-08-31")

    rows = {}
    mults = {t: multiples(pit[t], months, px.get(t, {})) for t in tickers}
    for i, ym in enumerate(months):
        for t in tickers:
            sc = scores_at(pit[t], ym)
            if sc:
                rows[(ym, t)] = (sc[0], sc[1], value_score_at(mults[t], months, i))
    print(f"\n평가 구간 {months[0][:7]} ~ {months[-1][:7]} · 관측 {len(rows)}건")

    run(tickers, px, months, rows, "전체 종목")
    # 2010년 이전 상장 종목만 — 최근 상장한 투기성 종목의 영향을 배제
    mature = [t for t in tickers if px.get(t) and min(px[t]) < "2010-01"]
    run(mature, px, months, rows, "2010년 이전 상장 종목만")


if __name__ == "__main__":
    main()
