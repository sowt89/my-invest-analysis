"""역발상 종합 점수 장기 백테스트 (최대 25년, 닷컴버블·금융위기 포함).

이 방식은 예측력이 음수로 측정돼 서비스에서 제외됐다.
같은 결론을 재현하기 위한 근거 스크립트로만 남긴다.
"""
import sys, os, math, pickle, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_data import compute_rsi, make_session
from legacy_score import (pt_ret12, pt_drawdown, pt_rsi, pt_volume,
                          pt_spy, pt_vix, pt_fg, band)

def load(symbols, period="max"):
    import yfinance as yf, time
    sess=make_session(); out={}
    for sym in symbols:
        h=yf.Ticker(sym,session=sess).history(period=period,interval="1d")
        out[sym]={"close":{d.date().isoformat():float(c) for d,c in zip(h.index,h["Close"])},
                  "vol":{d.date().isoformat():float(v) for d,v in zip(h.index,h["Volume"])}}
        ds=sorted(out[sym]["close"]); print(f"  {sym:6s} {len(ds):>5}일  {ds[0]} → {ds[-1]}")
        time.sleep(0.3)
    return out

def build_fg(px, days):
    """F&G 프록시. 성분별 z-score 평균 → 0~100. TLT 없는 구간(2002 이전)은
    사용 가능한 성분만으로 계산."""
    spy,vix=px["SPY"]["close"],px["^VIX"]["close"]
    tlt=px.get("TLT",{}).get("close",{})
    avg=lambda sr,i,n:(lambda w:sum(w)/len(w))([sr[d] for d in days[max(0,i-n+1):i+1] if d in sr])
    raw={}
    for i,d in enumerate(days):
        if i<130 or d not in spy or d not in vix: continue
        c=[spy[d]/avg(spy,i,125)-1, -(vix[d]/avg(vix,i,50)-1)]
        j=days[i-20]
        if d in tlt and j in tlt and j in spy:
            c.append((spy[d]/spy[j]-1)-(tlt[d]/tlt[j]-1))
        else: c.append(None)
        raw[d]=c
    stats=[]
    for k in range(3):
        vals=[v[k] for v in raw.values() if v[k] is not None]
        stats.append((st.mean(vals), st.pstdev(vals)))
    fg={}
    for d,c in raw.items():
        zs=[(v-m)/sd for v,(m,sd) in zip(c,stats) if v is not None]
        fg[d]=max(0,min(100,50+sum(zs)/len(zs)*20))
    return fg

def daily_scores(px, fg, ticker):
    q,spy,vix=px[ticker]["close"],px["SPY"]["close"],px["^VIX"]["close"]
    qv=px[ticker]["vol"]
    days=sorted(set(q)&set(spy)&set(vix))
    out=[]
    for i,d in enumerate(days):
        if i<252: continue
        w=days[i-252:i+1]; closes=[q[x] for x in w]; price=closes[-1]
        rsi=compute_rsi([q[x] for x in days[i-125:i+1]])
        vols=[qv[x] for x in days[i-79:i+1]]
        recent,prior=vols[-20:],vols[:-20]
        volch=(sum(recent)/len(recent))/(sum(prior)/len(prior))*100-100 if prior and sum(prior)>0 else None
        spy_w=[spy[x] for x in w]
        raw_mkt=pt_spy((spy_w[-1]/max(spy_w)-1)*100)+pt_vix(vix[d])+pt_fg(fg.get(d))
        ma200_spy=spy_w[-1]>sum(spy_w[-200:])/200
        adj=math.trunc(raw_mkt/2) if (ma200_spy and raw_mkt<0) else raw_mkt
        pts=pt_ret12((price/closes[0]-1)*100)+pt_drawdown((price/max(closes)-1)*100)+pt_rsi(rsi)+pt_volume(volch)
        ma200=sum(closes[-200:])/200
        out.append({"date":d,"price":price,"score":pts+adj,"ma200":ma200})
    return out

def strat(rows, buy_th=4, sell_th=-6):
    """현재 앱 설정: 매수 +4 / 매도 -6 + 종목 200일선 아래"""
    cash,sh,prev,pend=1.0,0.0,False,None; n=0; eq=[]
    for r in rows:
        if pend:
            if pend=="buy" and cash>0: sh,cash=cash/r["price"],0; n+=1
            elif pend=="sell" and sh>0: cash,sh=sh*r["price"],0
            pend=None
        eq.append(cash+sh*r["price"])
        s=r["score"]>=buy_th
        if s and not prev and cash>0: pend="buy"
        elif sh>0 and r["score"]<=sell_th and r["price"]<r["ma200"]: pend="sell"
        prev=s
    return cash+sh*rows[-1]["price"], n, eq

def mdd(eq):
    pk=eq[0]; w=0
    for v in eq: pk=max(pk,v); w=min(w,v/pk-1)
    return w*100
