#!/usr/bin/env python3
"""나만의 투자분석 — 실데이터 수집 스크립트.

yfinance로 워치리스트 39종목의 시세·1년 주가·재무·마진·컨센서스와
시장지표(^VIX, ^IXIC, ^GSPC, SPY 52주 낙폭, CNN Fear & Greed)를 수집해
data.json으로 저장한다. QQQ 포함 40개 티커 중 지수 ETF는 순위에서 제외한다.

매매 판단은 모멘텀 횡단면 순위(모멘텀 + 200일선 이격도)를 기준으로 한다.
모멘텀 = 최근 1개월을 제외한 12개월 수익률(12-1 모멘텀, 단기 반전 효과 제거).
26년 백테스트에서 4개 시대 모두 균등보유를 앞선 유일한 방식이다.
  순위 구간은 상위 5 / 6~15위 / 16위↓ 로만 표시하며 매매 지시가 아니다.

실적·밸류·재무 3축 점수는 참고 지표다. SEC 원본 재무제표로 2010~2026년을
검증한 결과 실적·재무 점수의 예측력은 확인되지 않았다(scripts/fin_ic.py).
역발상 10지표 종합 점수는 예측력이 음수로 측정돼 상세 탭 참고값으로만 남긴다.
"""

import json
import math
import os
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta

import requests as std_requests
import yfinance as yf
from curl_cffi import requests as curl_requests

# ---------------------------------------------------------------- watchlist
WATCHLIST = [
    ("QQQ", "NASDAQ 100", "지수 ETF"),  # TIGER 미국나스닥100 참고용
    ("NVDA", "NVIDIA", "매그니피센트7"),
    ("AAPL", "Apple", "매그니피센트7"),
    ("MSFT", "Microsoft", "매그니피센트7"),
    ("GOOGL", "Alphabet", "매그니피센트7"),
    ("AMZN", "Amazon", "매그니피센트7"),
    ("META", "Meta Platforms", "매그니피센트7"),
    ("TSLA", "Tesla", "매그니피센트7"),
    ("AVGO", "Broadcom", "반도체"),
    ("AMD", "AMD", "반도체"),
    ("ARM", "Arm Holdings", "반도체"),
    ("QCOM", "Qualcomm", "반도체"),
    ("ORCL", "Oracle", "SW 주식"),
    ("PLTR", "Palantir", "SW 주식"),
    ("NEE", "NextEra Energy", "전력"),
    ("CEG", "Constellation Energy", "전력"),
    ("SMR", "NuScale Power", "전력"),
    ("NXT", "Nextracker", "전력"),
    ("CVX", "Chevron", "전력"),
    ("RKLB", "Rocket Lab", "우주"),
    ("LUNR", "Intuitive Machines", "우주"),
    ("RDW", "Redwire", "우주"),
    ("IONQ", "IonQ", "양자"),
    ("INFQ", "Infleqtion", "양자"),
    ("JOBY", "Joby Aviation", "드론"),
    ("PL", "Planet Labs", "우주"),
    ("MU", "Micron Technology", "반도체"),
    ("INTC", "Intel", "반도체"),
    ("SNPS", "Synopsys", "반도체"),
    ("CDNS", "Cadence Design", "반도체"),
    ("CRM", "Salesforce", "SW 주식"),
    ("SPCX", "SpaceX", "우주"),
    ("NFLX", "Netflix", "커뮤니케이션"),
    ("NKE", "Nike", "소비재"),
    ("DIS", "Walt Disney", "커뮤니케이션"),
    ("UBER", "Uber", "기술"),
    ("ISRG", "Intuitive Surgical", "헬스케어"),
    ("LMT", "Lockheed Martin", "산업재"),
    ("COIN", "Coinbase", "금융"),
    ("BRK-B", "Berkshire Hathaway", "금융"),
]

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(_ROOT, "data.json")
HIST_PATH = os.path.join(_ROOT, "history.json")
PIT_PATH = os.path.join(_ROOT, "data", "sec_pit.json")
HIST_DAYS = 1095  # 3년 (초과분은 archive/history-YYYY.json으로 이관)
ARCHIVE_DIR = os.path.join(_ROOT, "archive")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


# ---------------------------------------------------------------- session
def make_session():
    """야후 접속용 curl_cffi 세션. 브라우저 TLS 지문(impersonate)이 막히는
    프록시 환경에서는 일반 세션으로 폴백한다."""
    ca = os.environ.get("YF_CA_BUNDLE")
    if not ca and os.path.exists("/root/.ccr/ca-bundle.crt"):
        ca = "/root/.ccr/ca-bundle.crt"
    verify = ca if ca else True
    test_url = "https://query1.finance.yahoo.com/v8/finance/chart/SPY?range=1d&interval=1d"
    try:
        s = curl_requests.Session(impersonate="chrome", verify=verify)
        s.get(test_url, timeout=15).raise_for_status()
        print("session: curl_cffi (impersonate=chrome)")
        return s
    except Exception:
        pass
    s = curl_requests.Session(verify=verify, headers={"User-Agent": UA})
    print("session: curl_cffi (plain)")
    return s


# ---------------------------------------------------------------- helpers
def rnd(x, d=2):
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return round(x, d)


def pick_row(df, names):
    """재무제표 DataFrame에서 이름 후보 중 첫 매칭 행을 반환."""
    if df is None or getattr(df, "empty", True):
        return None
    for n in names:
        if n in df.index:
            return df.loc[n]
    return None


def series_vals(row, cols):
    out = []
    for c in cols:
        try:
            v = row[c]
            out.append(None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v))
        except Exception:
            out.append(None)
    return out


def closes_of(h):
    """가격 히스토리에서 NaN(미확정 당일 봉)을 제외한 종가 리스트."""
    if h is None or len(h) == 0:
        return []
    return [float(x) for x in h["Close"] if x == x]


def compute_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100 - 100 / (1 + rs)


def retry(fn, tries=3, delay=2.0, default=None):
    for i in range(tries):
        try:
            return fn()
        except Exception:
            if i == tries - 1:
                return default
            time.sleep(delay * (2 ** i))
    return default


# ---------------------------------------------------------------- scoring rules
# 9개 지표: 종목별 6개 + 시장 타이밍 3개(전 종목 공통 가산)

def growth_score(d):
    """실적 점수 (-7 ~ +8). 매출 성장·어닝 서프라이즈·마진 방향.
    ※ 과거 데이터 재현 불가로 백테스트 미검증 참고 지표."""
    pts = {}
    yoy = d.get("revG", [None]*3)[1]
    pts["yoy"] = 0 if yoy is None else (2 if yoy >= 25 else 1 if yoy >= 10 else
                                        -2 if yoy <= -10 else -1 if yoy < 0 else 0)
    cagr = d.get("revG", [None]*3)[2]
    pts["cagr"] = 0 if cagr is None else (2 if cagr >= 20 else 1 if cagr >= 10 else -1 if cagr < 0 else 0)
    sp = d.get("surprise")
    pts["surprise"] = 0 if sp is None else (1 if sp >= 10 else -2 if sp <= -10 else -1 if sp < 0 else 0)
    fg_ = d["est"][0].get("g") if d.get("est") else None
    pts["fwdGrowth"] = 0 if fg_ is None else (2 if fg_ >= 20 else 1 if fg_ >= 10 else -1 if fg_ < 0 else 0)
    # 영업이익률 방향: 최근 분기 vs 1년 전 같은 분기
    mt = None
    qr, qo = d.get("qRev") or [], d.get("qOp") or []
    if len(qr) >= 5 and qr[-1] and qr[-5] and qo[-1] is not None and qo[-5] is not None:
        mt = (qo[-1] / qr[-1] - qo[-5] / qr[-5]) * 100
    pts["marginTrend"] = 0 if mt is None else (1 if mt >= 2 else -1 if mt <= -2 else 0)
    return sum(pts.values()), pts


def valuation_score(d):
    """밸류 점수 (-5 ~ +6). 낮은 가격에 거래되는가.
    ※ 백테스트 미검증 참고 지표."""
    pts = {}
    g = d.get("gap")
    pts["perGap"] = 0 if g is None else (2 if g <= -20 else 1 if g <= -5 else
                                         -2 if g >= 30 else -1 if g >= 10 else 0)
    psr, avg = d.get("psr"), d.get("avgPsr")
    pr = (psr / avg - 1) * 100 if (psr and avg) else None
    pts["psrGap"] = 0 if pr is None else (1 if pr <= -20 else -1 if pr >= 50 else 0)
    f = d.get("fcfY")
    pts["fcfYield"] = 0 if f is None else (2 if f >= 5 else 1 if f >= 3 else -1 if f < 0 else 0)
    peg = d.get("peg")
    pts["peg"] = 0 if peg is None else (1 if peg < 1 else -1 if peg > 2.5 else 0)
    return sum(pts.values()), pts


def finance_score(d):
    """재무 점수 (-6 ~ +7). 수익성과 재무 건전성.
    ※ 백테스트 미검증 참고 지표."""
    pts = {}
    op = d.get("op")
    pts["opMargin"] = 0 if op is None else (2 if op >= 30 else 1 if op >= 15 else -2 if op < 0 else 0)
    roe = d.get("roe")
    pts["roe"] = 0 if roe is None else (2 if roe >= 30 else 1 if roe >= 15 else -1 if roe < 0 else 0)
    de = d.get("ltDE")
    pts["debt"] = 0 if de is None else (1 if de <= 0.5 else -1 if de >= 2 else 0)
    cr = d.get("curR")
    pts["current"] = 0 if cr is None else (1 if cr >= 1.5 else -1 if cr < 1 else 0)
    fm = d.get("fcfM")
    pts["fcfMargin"] = 0 if fm is None else (1 if fm >= 15 else -1 if fm < 0 else 0)
    return sum(pts.values()), pts


def fg_label(v):
    if v is None: return "정보 없음"
    if v <= 25: return "극단적 공포"
    if v <= 45: return "공포"
    if v <= 55: return "중립"
    if v <= 75: return "탐욕"
    return "극단적 탐욕"


# ---------------------------------------------------------------- market
def fetch_fear_greed():
    try:
        r = std_requests.get(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            headers={"User-Agent": UA, "Accept": "application/json"}, timeout=20)
        r.raise_for_status()
        return float(r.json()["fear_and_greed"]["score"])
    except Exception as e:
        print(f"  ! Fear&Greed 수집 실패: {e}")
        return None


def fetch_market(session):
    out = {}
    def last_close(sym, period="5d"):
        c = closes_of(yf.Ticker(sym, session=session).history(period=period, interval="1d"))
        return c[-1] if c else None

    out["vix"] = rnd(retry(lambda: last_close("^VIX")), 2)
    out["nasdaq"] = rnd(retry(lambda: last_close("^IXIC")), 0)
    out["sp500"] = rnd(retry(lambda: last_close("^GSPC")), 0)

    spy_dd = spy_above_ma200 = None
    try:
        h = yf.Ticker("SPY", session=session).history(period="1y", interval="1d")
        closes = closes_of(h)
        if closes:
            hi = max(closes)
            cur = closes[-1]
            spy_dd = (cur / hi - 1) * 100
            if len(closes) >= 200:
                spy_above_ma200 = cur > sum(closes[-200:]) / 200
                out["spy_golden"] = (sum(closes[-50:]) / 50) > (sum(closes[-200:]) / 200)
    except Exception as e:
        print(f"  ! SPY 수집 실패: {e}")
    out["spy_dd_52w"] = rnd(spy_dd, 1)
    out["spy_ma200_above"] = spy_above_ma200

    fg = fetch_fear_greed()
    out["fear_greed"] = rnd(fg, 0)
    out["fg_label"] = fg_label(fg)

    return out


# ---------------------------------------------------------------- per stock
def load_history():
    """일별 FWD PER 스냅샷 {"YYYY-MM-DD": {"NVDA": 17.5, ...}}."""
    try:
        with open(HIST_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def score_inputs(d):
    """3축 점수의 원자료. 나중에 기준을 바꿔 재채점·예측력(IC) 측정을 하기 위해 함께 남긴다."""
    psr, apsr = d.get("psr"), d.get("avgPsr")
    qr, qo = d.get("qRev") or [], d.get("qOp") or []
    mt = None
    if len(qr) >= 5 and qr[-1] and qr[-5] and qo[-1] is not None and qo[-5] is not None:
        mt = rnd((qo[-1] / qr[-1] - qo[-5] / qr[-5]) * 100, 2)
    rev = d.get("revG", [None] * 3)
    raw = {"yoy": rev[1], "cagr": rev[2], "sp": d.get("surprise"),
           "fwdG": d["est"][0].get("g") if d.get("est") else None, "mt": mt,
           "gap": d.get("gap"), "psrG": rnd((psr / apsr - 1) * 100, 1) if (psr and apsr) else None,
           "fcfY": d.get("fcfY"), "peg": d.get("peg"),
           "op": d.get("op"), "roe": d.get("roe"), "de": d.get("ltDE"),
           "cr": d.get("curR"), "fcfM": d.get("fcfM")}
    return {k: v for k, v in raw.items() if v is not None}


def archive_old(rows):
    """보관 기간이 지난 스냅샷을 삭제하지 않고 연도별 파일로 옮겨 연속성을 유지한다."""
    if not rows:
        return
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    for year in sorted({d[:4] for d in rows}):
        path = os.path.join(ARCHIVE_DIR, f"history-{year}.json")
        try:
            with open(path, encoding="utf-8") as f:
                cur = json.load(f)
        except Exception:
            cur = {}
        cur.update({d: v for d, v in rows.items() if d[:4] == year})
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dict(sorted(cur.items())), f, ensure_ascii=False, separators=(",", ":"))
        print(f"  아카이브 {os.path.basename(path)}: {len(cur)}일")


def save_history(hist, stocks):
    """오늘 첫 실행에만 스냅샷을 추가하고 3년 초과분은 제거."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prev_today = hist.get(today)
    complete = lambda k: any(isinstance(r, dict) and r.get(k) is not None
                             for r in (prev_today or {}).values())
    if today not in hist or not complete("rank") or not complete("gs"):
        snap = {}
        for s in stocks:
            d = s["detail"]
            eps = d["est"][0].get("eps") if d.get("est") else None  # 당해연도 EPS 컨센서스 (리비전 추적용)
            rec = {"per": d.get("fwdPer"), "eps": eps,
                   "rank": s.get("mom_rank"), "mom": s.get("mom_score"),
                   "px": s.get("price"),                      # 사후 수익률 계산용
                   "gs": s.get("grow_score"), "vs": s.get("val_score"),
                   "fs": s.get("fin_score"), "raw": score_inputs(d) or None}
            if any(v is not None for v in rec.values()):
                snap[s["ticker"]] = rec
        if snap:
            hist[today] = snap   # 같은 날 재수집 시 누락 필드(순위 등)를 채운다
    cutoff = (datetime.now(timezone.utc) - timedelta(days=HIST_DAYS)).strftime("%Y-%m-%d")
    archive_old({d: v for d, v in hist.items() if d < cutoff})
    hist = {d: v for d, v in hist.items() if d >= cutoff}
    with open(HIST_PATH, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(hist.items())), f, ensure_ascii=False, separators=(",", ":"))
    return hist


def hist_avg_fwd_per(hist, ticker):
    """누적 실측 평균과 (실측 기간/3년) 가중치를 반환."""
    vals = []
    for d, v in hist.items():
        e = v.get(ticker)
        if e is None:
            continue
        per = e if isinstance(e, (int, float)) else e.get("per")  # 구버전은 숫자, 신버전은 dict
        if per is not None:
            vals.append((d, per))
    if not vals:
        return None, 0.0
    avg = sum(v for _, v in vals) / len(vals)
    span = (datetime.now(timezone.utc)
            - datetime.strptime(min(d for d, _ in vals), "%Y-%m-%d").replace(tzinfo=timezone.utc)).days
    return avg, min(1.0, span / HIST_DAYS)


def load_pit():
    """SEC 원본 재무 시계열(data/sec_pit.json). 없으면 빈 dict."""
    try:
        with open(PIT_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def sec_avg_psr(rows, dates, closes, psr_now):
    """과거 PSR 실측 평균 = 각 시점 주가 x 당시 발행주식수 / 당시 TTM 매출.

    근사값(현재 매출을 과거 주가에 대입)과 달리 매출 성장이 반영된다.
    현재 시점 재계산값이 야후의 PSR과 30% 넘게 어긋나면(주식 클래스 분리 등)
    신뢰할 수 없다고 보고 None을 반환해 기존 근사값으로 되돌린다.
    """
    usable = [r for r in rows if r.get("sh") and r.get("revTtm")]
    if len(usable) < 8 or not dates:
        return None
    vals, idx = [], 0
    for d, px in zip(dates, closes):
        while idx + 1 < len(usable) and usable[idx + 1]["filed"] <= d:
            idx += 1
        r = usable[idx]
        if r["filed"] <= d:
            vals.append(px * r["sh"] / r["revTtm"])
    if len(vals) < 20:
        return None
    if psr_now:
        last = usable[-1]
        check = closes[-1] * last["sh"] / last["revTtm"]
        if abs(check / psr_now - 1) > 0.3:
            return None
    return sum(vals) / len(vals)


def fetch_stock(session, ticker, name, theme, market, hist, pit):
    tk = yf.Ticker(ticker, session=session)
    info = retry(lambda: tk.info, default={}) or {}

    # ---- 가격 히스토리 (5년 주봉: 1년 차트 + 3Y/5Y 평균 프록시, 6개월 일봉: RSI·거래량)
    h5 = retry(lambda: tk.history(period="5y", interval="1wk"))
    hd = retry(lambda: tk.history(period="6mo", interval="1d"))

    hd_closes = closes_of(hd)
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    if price is None and hd_closes:
        price = hd_closes[-1]
    if price is None:
        raise RuntimeError("가격 정보 없음")
    price = float(price)

    prices, price_dates, price_dates_5y = [], [], []
    ret12 = dd = ytd = None
    avg3y_px = avg5y_px = None
    closes5_all = closes_of(h5)
    if len(closes5_all) > 5:
        closes5 = closes5_all
        dates5 = [d.strftime("%Y-%m-%d") for d, x in zip(h5.index, h5["Close"]) if x == x]
        price_dates_5y = dates5
        n1y = min(53, len(closes5))
        prices = [rnd(x, 2) for x in closes5[-n1y:]]
        price_dates = dates5[-n1y:]
        if len(closes5) > n1y - 1:
            ret12 = (price / closes5[-n1y] - 1) * 100
        hi52 = max(closes5[-n1y:] + [price])
        dd = (price / hi52 - 1) * 100
        year = datetime.now(timezone.utc).year
        ytd_base = next((c for d, c in zip(dates5, closes5) if d >= f"{year}-01-01"), None)
        if ytd_base:
            ytd = (price / ytd_base - 1) * 100
        avg3y_px = sum(closes5[-min(157, len(closes5)):]) / min(157, len(closes5))
        avg5y_px = sum(closes5) / len(closes5)

    wk = closes5_all   # 주봉 종가 (미확정 봉 제외)
    above_ma200 = mom12_1 = ma200_dist = None
    if len(wk) >= 40:
        ma200 = sum(wk[-40:]) / 40            # 주봉 40주 ≈ 200거래일
        above_ma200 = price > ma200
        ma200_dist = (price / ma200 - 1) * 100
    if len(wk) >= 53:
        # 12-1 모멘텀: 최근 1개월(4주) 제외한 12개월 수익률
        mom12_1 = (wk[-5] / wk[-53] - 1) * 100

    rsi = vol_ch = None
    if len(hd_closes) > 20:
        rsi = compute_rsi(hd_closes)
        # 종가가 유효한 봉만 사용 — 미확정 당일 봉의 부분 거래량이 섞이면
        # 최근 20일 평균이 과소평가돼 거래량 급증을 놓친다
        vols = [float(v) for c, v in zip(hd["Close"], hd["Volume"]) if c == c and v == v]
        if len(vols) >= 40:
            recent = vols[-20:]
            prior = vols[-80:-20] if len(vols) >= 80 else vols[:-20]
            if prior and sum(prior) > 0:
                vol_ch = (sum(recent) / len(recent)) / (sum(prior) / len(prior)) * 100 - 100

    # ---- 밸류에이션·퀄리티 (info)
    mcap = info.get("marketCap")
    fwd_per = info.get("forwardPE")
    fwd_per_negative = fwd_per is not None and fwd_per <= 0
    if fwd_per_negative:
        fwd_per = None  # 적자(예상 EPS 음수) → 결측 처리
    # 3Y 평균 FWD PER: 일별 실측 누적(history.json)과 프록시(현재 예상 EPS 기준
    # 과거 3년 평균 주가 환산)를 누적 기간 비중으로 혼합. 누적될수록 실측이 대체.
    proxy_avg = (fwd_per * avg3y_px / price) if (fwd_per and avg3y_px) else None
    h_avg, h_w = hist_avg_fwd_per(hist, ticker)
    if h_avg is not None and proxy_avg is not None:
        avg_fwd_per = h_w * h_avg + (1 - h_w) * proxy_avg
    else:
        avg_fwd_per = h_avg if h_avg is not None else proxy_avg
    gap = ((fwd_per / avg_fwd_per - 1) * 100) if (fwd_per and avg_fwd_per) else None

    psr = info.get("priceToSalesTrailing12Months")
    # 5년 평균 PSR: SEC 원본(주식수·매출) 실측을 우선하고, 불가하면 근사값으로 대체
    avg_psr = sec_avg_psr(pit.get(ticker) or [], price_dates_5y, closes5_all, psr)
    psr_src = "sec" if avg_psr is not None else "proxy"
    if avg_psr is None:
        avg_psr = (psr * avg5y_px / price) if (psr and avg5y_px) else None
    total_rev = info.get("totalRevenue")
    fcf = info.get("freeCashflow")
    short_ratio = info.get("shortRatio")

    pct100 = lambda v: None if v is None else v * 100
    d = {
        "ret12": rnd(ret12, 1), "dd": rnd(dd, 1), "rsi": rnd(rsi, 1),
        "shortR": rnd(short_ratio, 2), "volCh": rnd(vol_ch, 1),
        "fwdPer": rnd(fwd_per, 1), "avgFwdPer": rnd(avg_fwd_per, 1),
        "psr": rnd(psr, 2), "avgPsr": rnd(avg_psr, 2), "psrSrc": psr_src,
        "evEbitda": rnd(info.get("enterpriseToEbitda"), 2),
        "peg": rnd(info.get("trailingPegRatio") or info.get("pegRatio"), 2),
        "fcfY": rnd(pct100(fcf / mcap) if (fcf and mcap) else None, 2),
        "roa": rnd(pct100(info.get("returnOnAssets")), 1),
        "roe": rnd(pct100(info.get("returnOnEquity")), 1),
        "gross": rnd(pct100(info.get("grossMargins")), 1),
        "op": rnd(pct100(info.get("operatingMargins")), 1),
        "profit": rnd(pct100(info.get("profitMargins")), 1),
        "fcfM": rnd(pct100(fcf / total_rev) if (fcf and total_rev) else None, 1),
        "curR": rnd(info.get("currentRatio"), 2),
        "quickR": rnd(info.get("quickRatio"), 2),
        "ltDE": rnd((info.get("debtToEquity") or 0) / 100 or None, 2),  # yfinance debtToEquity = 총부채/자기자본
        "prices": prices, "priceDates": price_dates,
        "mom12_1": rnd(mom12_1, 1), "ma200Dist": rnd(ma200_dist, 1),
    }
    # FWD PSR = 시총 / 당해연도 예상 매출 (아래 컨센서스에서 채움)
    d["fwdPsr"] = None

    # ---- 분기·연간 실적
    B = 1e9
    qi = retry(lambda: tk.quarterly_income_stmt)
    d["qLabels"], d["qRev"], d["qOp"] = [], [], []
    if qi is not None and not getattr(qi, "empty", True):
        cols = sorted(qi.columns)[-8:]
        rev = pick_row(qi, ["Total Revenue", "Operating Revenue"])
        op = pick_row(qi, ["Operating Income", "Total Operating Income As Reported", "EBIT"])
        if rev is not None:
            d["qLabels"] = [f"{c.year % 100}Q{(c.month - 1) // 3 + 1}" for c in cols]
            d["qRev"] = [rnd(v / B, 2) if v is not None else None for v in series_vals(rev, cols)]
            d["qOp"] = ([rnd(v / B, 2) if v is not None else None for v in series_vals(op, cols)]
                        if op is not None else [None] * len(cols))

    yi = retry(lambda: tk.income_stmt)
    cf = retry(lambda: tk.cashflow)
    d["yrs"], d["yRev"], d["yOcf"], d["yCapex"] = [], [], [], []
    rev_hist = []
    if yi is not None and not getattr(yi, "empty", True):
        cols = sorted(yi.columns)[-4:]
        rev = pick_row(yi, ["Total Revenue", "Operating Revenue"])
        ocf = pick_row(cf, ["Operating Cash Flow"]) if cf is not None else None
        capex = pick_row(cf, ["Capital Expenditure"]) if cf is not None else None
        if rev is not None:
            d["yrs"] = [f"FY{c.year % 100}" for c in cols]
            rev_hist = series_vals(rev, cols)
            d["yRev"] = [rnd(v / B, 2) if v is not None else None for v in rev_hist]
            for key, row in (("yOcf", ocf), ("yCapex", capex)):
                d[key] = ([rnd(v / B, 2) if v is not None else None for v in series_vals(row, cols)]
                          if row is not None else [None] * len(cols))

    # 매출 성장률: [최근-2 YoY, 최근 YoY, 3Y CAGR]
    d["revG"] = [None, None, None]
    vals = [v for v in rev_hist if v]
    if len(vals) >= 2:
        d["revG"][1] = rnd((vals[-1] / vals[-2] - 1) * 100, 1)
    if len(vals) >= 3:
        d["revG"][0] = rnd((vals[-2] / vals[-3] - 1) * 100, 1)
    if len(vals) >= 4 and vals[-4] > 0:
        d["revG"][2] = rnd(((vals[-1] / vals[-4]) ** (1 / 3) - 1) * 100, 1)

    # ---- 컨센서스 (당해·차기 연도)
    d["est"] = []
    rev_est = retry(lambda: tk.revenue_estimate)
    eps_est = retry(lambda: tk.earnings_estimate)
    cur_year = datetime.now(timezone.utc).year
    try:
        if rev_est is not None and not rev_est.empty:
            for i, period in enumerate(["0y", "+1y"]):
                if period not in rev_est.index:
                    continue
                r = rev_est.loc[period]
                e = eps_est.loc[period] if (eps_est is not None and period in eps_est.index) else None
                rev_avg = r.get("avg")
                if rev_avg is None or (isinstance(rev_avg, float) and math.isnan(rev_avg)):
                    continue
                d["est"].append({
                    "y": f"FY{(cur_year + i) % 100}E",
                    "rev": rnd(rev_avg / B, 1),
                    "g": rnd(r.get("growth") * 100, 1) if r.get("growth") is not None else None,
                    "eps": rnd(e.get("avg"), 2) if e is not None else None,
                    "epsG": (rnd(e.get("growth") * 100, 1)
                             if (e is not None and e.get("growth") is not None) else None),
                })
            if d["est"] and mcap:
                fwd_rev = d["est"][0]["rev"]
                if fwd_rev:
                    d["fwdPsr"] = rnd(mcap / (fwd_rev * B), 2)
    except Exception:
        pass

    # 예상 실적 연도를 연간 실적 차트에 연결
    if d["est"] and d["yrs"]:
        d["yrs"].append(d["est"][0]["y"])
        d["yRev"].append(d["est"][0]["rev"])
        d["yOcf"].append(None)
        d["yCapex"].append(None)

    # ---- 실적 이벤트: 다음 발표일(D-day 표시용), 최근 분기 어닝 서프라이즈
    earnings_date = earnings_in = surprise = None
    cal = retry(lambda: tk.calendar, default={}) or {}
    try:
        dates = [x.date() if isinstance(x, datetime) else x
                 for x in (cal.get("Earnings Date") or [])]
        today = datetime.now(timezone.utc).date()
        future = sorted(x for x in dates if x >= today)
        if future:
            earnings_date = future[0].isoformat()
            earnings_in = (future[0] - today).days
    except Exception:
        pass
    eh = retry(lambda: tk.earnings_history)
    try:
        if eh is not None and not eh.empty:
            for _, row in eh.iloc[::-1].iterrows():
                act, est = row.get("epsActual"), row.get("epsEstimate")
                if act is not None and est and not math.isnan(act) and not math.isnan(est):
                    surprise = (act - est) / abs(est) * 100
                    break
    except Exception:
        pass

    d.update({"gap": rnd(gap, 1), "surprise": rnd(surprise, 1),
              "earningsDate": earnings_date})

    gscore, gpts = growth_score(d)
    vscore, vpts = valuation_score(d)
    fscore, fpts = finance_score(d)
    d["growPts"], d["valPts"], d["finPts"] = gpts, vpts, fpts

    if d["gap"] is not None:
        cmt = (f"{name}의 FWD PER은 3년 평균 대비 {abs(d['gap']):.1f}% "
               f"{'낮은' if d['gap'] < 0 else '높은'} 수준입니다.")
    elif fwd_per_negative:
        cmt = f"{name}은(는) 예상 EPS가 음수여서 FWD PER을 산출할 수 없습니다."
    elif d["fwdPer"] is None:
        cmt = f"{name}은(는) FWD PER 정보가 제공되지 않습니다."
    else:
        cmt = f"{name}의 FWD PER은 {d['fwdPer']}배입니다."

    return {
        "ticker": ticker, "name": name, "theme": theme,
        "earnings_in": earnings_in, "above_ma200": above_ma200,
        "grow_score": gscore, "val_score": vscore, "fin_score": fscore,
        "total_score": gscore + vscore + fscore,
        "mom_score": (rnd(0.5 * mom12_1 + 0.5 * ma200_dist, 1)
                      if (mom12_1 is not None and ma200_dist is not None) else None),
        "price": rnd(price, 2),
        "market_cap_b": rnd(mcap / B, 1) if mcap else None,
        "ytd": rnd(ytd, 1),
        "detail": {**d, "cmt": cmt},
    }


# ---------------------------------------------------------------- main
def main():
    session = make_session()
    print("시장지표 수집…")
    market = fetch_market(session)
    print(f"  VIX {market['vix']} / SPY 52주 {market['spy_dd_52w']}% / "
          f"F&G {market['fear_greed']}({market['fg_label']})")

    hist = load_history()
    pit = load_pit()
    print(f"SEC 원본 재무: {len(pit)}종목 (data/sec_pit.json)")
    stocks, failed = [], []
    for i, (ticker, name, theme) in enumerate(WATCHLIST):
        try:
            s = fetch_stock(session, ticker, name, theme, market, hist, pit)
            stocks.append(s)
            print(f"  [{i+1:2d}/{len(WATCHLIST)}] {ticker:6s} ${s['price']:>9.2f}  "
                  f"3축 {s['grow_score']:+d}/{s['val_score']:+d}/{s['fin_score']:+d}")
        except Exception as e:
            failed.append(ticker)
            print(f"  [{i+1:2d}/{len(WATCHLIST)}] {ticker:6s} 실패: {e}")
            traceback.print_exc(limit=1)
        time.sleep(0.5)

    if len(stocks) < len(WATCHLIST) * 0.6:
        print(f"오류: 성공 종목이 {len(stocks)}개뿐이라 data.json을 갱신하지 않습니다.")
        sys.exit(1)

    # 시장 국면: SPY 200일선 × 시장 폭 (26년 검증 — 상승장 3개월 +3.3%/손실 27%,
    # 하락장 +1.5%/42%. VIX 30↑은 역발상 반등 구간 +10.6%)
    ma_flags = [x["above_ma200"] for x in stocks if x["above_ma200"] is not None
                and x["theme"] != "지수 ETF"]
    breadth = round(sum(ma_flags) / len(ma_flags) * 100) if ma_flags else None
    market["breadth_pct"] = breadth
    spy_up = market.get("spy_ma200_above")
    if spy_up is None or breadth is None:
        market["regime"] = "판단 불가"
    elif spy_up and breadth >= 55:
        market["regime"] = "상승장"
    elif not spy_up and breadth < 45:
        market["regime"] = "하락장"
    else:
        market["regime"] = "혼조"
    market["panic"] = bool(market.get("vix") and market["vix"] >= 30)

    # 모멘텀 횡단면 순위 (1위가 가장 강함) — 지수 ETF는 제외
    ranked = sorted([x for x in stocks if x.get("mom_score") is not None
                     and x["theme"] != "지수 ETF"],
                    key=lambda x: -x["mom_score"])
    total = len(ranked)
    for i, x in enumerate(ranked):
        r = i + 1
        x["mom_rank"] = r
        x["mom_total"] = total
        # 백테스트 검증 구간: 상위 5종목 보유 · 월 1회 교체
        x["mom_band"] = "상위 5" if r <= 5 else ("6~15위" if r <= 15 else "16위↓")

    top5 = [x for x in ranked[:5]]
    if top5:
        from collections import Counter
        theme_cnt = Counter(x["theme"] for x in top5)
        top_theme, top_n = theme_cnt.most_common(1)[0]
        market["top5_theme"] = top_theme
        market["top5_theme_count"] = top_n

    now = datetime.now(timezone.utc)
    payload = {
        "updated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "updated_at_kst": (now + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M"),
        "source": "yfinance + CNN Fear & Greed",
        "market": market,
        "stocks": stocks,
        "failed_tickers": failed,
    }
    # 약 한 달 전 순위와 비교 (없으면 가장 오래된 기록)
    past_key = None
    today_key = now.strftime("%Y-%m-%d")
    has_rank = [d for d, v in hist.items() if d != today_key
                and any(isinstance(r, dict) and r.get("rank") for r in v.values())]
    if has_rank:
        target = (now - timedelta(days=30)).strftime("%Y-%m-%d")
        older = sorted(d for d in has_rank if d <= target)
        past_key = older[-1] if older else min(has_rank)
    if past_key:
        payload["compare_date"] = past_key
        for s in stocks:
            rec = hist[past_key].get(s["ticker"])
            prev = rec.get("rank") if isinstance(rec, dict) else None
            s["prev_rank"] = prev

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    hist = save_history(hist, stocks)
    print(f"FWD PER 히스토리: {len(hist)}일 누적 (history.json)")
    size = os.path.getsize(OUT_PATH)
    print(f"저장 완료: {OUT_PATH} ({size/1024:.0f} KB, 종목 {len(stocks)}개, 실패 {failed})")


if __name__ == "__main__":
    main()
