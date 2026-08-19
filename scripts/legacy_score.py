#!/usr/bin/env python3
"""역발상 10지표 종합 점수 — 백테스트 전용.

검증 결과 예측력이 음수로 측정돼 서비스(fetch_data.py·화면)에서는 사용하지
않는다. 그 결론을 재현하는 backtest.py·longbt.py만 이 모듈을 쓴다.

지표: 12개월 수익률 · 52주 낙폭 · RSI · 공매도 비율 · 거래량 증가율 ·
      FWD PER 괴리 · 어닝 서프라이즈 + 시장 타이밍 3개(SPY 낙폭 · VIX · F&G)
"""

import math


def pt_ret12(v):        # 1) 12개월 수익률 — 과열은 감점, 급락은 역발상 가점
    if v is None: return 0
    if v <= -30: return 2
    if v <= -10: return 1
    if v >= 100: return -2
    if v >= 50: return -1
    return 0

def pt_drawdown(v):     # 2) 52주 고점 대비 낙폭
    if v is None: return 0
    if v <= -30: return 2
    if v <= -15: return 1
    if v >= -3: return -1
    return 0

def pt_rsi(v):          # 3) RSI(14)
    if v is None: return 0
    if v < 30: return 2
    if v < 40: return 1
    if v > 70: return -2
    if v > 60: return -1
    return 0

def pt_short(v):        # 4) 공매도 비율(days-to-cover)
    if v is None: return 0
    if v < 2: return 1
    if v > 8: return -1
    return 0

def pt_volume(v):       # 5) 거래량 증가율(최근 20일 vs 이전 60일)
    if v is None: return 0
    if v >= 100: return 2
    if v >= 30: return 1
    if v <= -40: return -1
    return 0

def pt_valuation(gap, fwd_per):  # 6) FWD PER 3년 평균 대비 괴리 — 결측(적자)은 0점
    if fwd_per is None or gap is None: return 0
    if gap <= -30: return 2
    if gap <= -10: return 1
    if gap >= 30: return -2
    if gap >= 10: return -1
    return 0

def pt_spy(dd):         # 7) SPY 52주 고점 대비 낙폭 (시장 공통)
    if dd is None: return 0
    if dd <= -15: return 2
    if dd <= -7: return 1
    if dd >= -2: return -1
    return 0

def pt_vix(v):          # 8) VIX (시장 공통, 역발상 — 극단에서만 감점)
    if v is None: return 0
    if v >= 30: return 2
    if v >= 24: return 1
    if v <= 12: return -1
    return 0

def pt_fg(v):           # 9) CNN Fear & Greed (시장 공통, 역발상 — 극단에서만 감점)
    if v is None: return 0
    if v <= 25: return 2
    if v <= 40: return 1
    if v >= 80: return -2
    if v >= 70: return -1
    return 0

def pt_surprise(v):     # 10) 최근 분기 어닝 서프라이즈 %
    if v is None: return 0
    if v >= 10: return 1
    if v <= -10: return -2
    if v < 0: return -1
    return 0


def band(score, above_ma200=False):
    """백테스트(38종목×63조합) 기반 구간.
    매도는 점수 -6 이하 + 종목이 자기 200일선 아래일 때만 (추세 존중)."""
    if score >= 4: return "강한 매수"
    if score >= 1: return "매수 대기"
    if score <= -6 and not above_ma200: return "매도 대기"
    return "관망"
