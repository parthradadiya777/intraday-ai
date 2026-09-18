import math, time
from datetime import datetime
import pandas as pd
import numpy as np
from nsemine import historical

BACKTEST_CACHE = {}
BACKTEST_TTL = 900
RISK_FREE = 0.06

SECTOR_MAP = {
'ADANIENT':'Diversified','ADANIPORTS':'Infrastructure','APOLLOHOSP':'Healthcare','ASIANPAINT':'Consumer','AXISBANK':'Banking',
'BAJAJ-AUTO':'Auto','BAJAJFINSV':'Financial Services','BAJFINANCE':'Financial Services','BEL':'Defence','BHARTIARTL':'Telecom',
'CIPLA':'Pharma','COALINDIA':'Energy','DRREDDY':'Pharma','EICHERMOT':'Auto','ETERNAL':'Consumer','GRASIM':'Cement',
'HCLTECH':'IT','HDFCBANK':'Banking','HDFCLIFE':'Insurance','HEROMOTOCO':'Auto','HINDALCO':'Metals','HINDUNILVR':'Consumer',
'ICICIBANK':'Banking','INDUSINDBK':'Banking','INFY':'IT','ITC':'Consumer','JIOFIN':'Financial Services','JSWSTEEL':'Metals',
'KOTAKBANK':'Banking','LT':'Infrastructure','M&M':'Auto','MARUTI':'Auto','NESTLEIND':'Consumer','NTPC':'Energy','ONGC':'Energy',
'POWERGRID':'Energy','RELIANCE':'Energy','SBILIFE':'Insurance','SBIN':'Banking','SHRIRAMFIN':'Financial Services','SUNPHARMA':'Pharma',
'TATACONSUM':'Consumer','TATAMOTORS':'Auto','TATASTEEL':'Metals','TCS':'IT','TECHM':'IT','TITAN':'Consumer','TRENT':'Consumer',
'ULTRACEMCO':'Cement','WIPRO':'IT'}

def norm(v):
    return str(v or '').strip().upper().replace('NSE:','').replace('NSE_','').replace('-EQ','').replace('.NS','')

def sector_for(symbol): return SECTOR_MAP.get(norm(symbol), 'Other')

def market_context(rows, nifty_symbols=None):
    nifty=set(norm(x) for x in (nifty_symbols or [])); vals=[]; sectors={}
    for r in rows or []:
        sym=norm(r.get('symbol'))
        try: ch=float(r.get('change'))
        except Exception: continue
        sec=sector_for(sym); sectors.setdefault(sec,[]).append(ch)
        if not nifty or sym in nifty: vals.append(ch)
    if not vals: vals=[float(r.get('change') or 0) for r in rows if r.get('change') is not None]
    mc=sum(vals)/len(vals) if vals else 0.0
    return {'market_change':round(mc,3),'market_trend':'BULLISH' if mc>.20 else 'BEARISH' if mc<-.20 else 'NEUTRAL',
            'sector_stats':{k:sum(v)/len(v) for k,v in sectors.items()}}

def enrich_row(row, ctx):
    sym=norm(row.get('symbol')); sec=sector_for(sym); sc=float(ctx.get('sector_stats',{}).get(sec,0))
    mc=float(ctx.get('market_change',0)); sig=str(row.get('signal') or 'WAIT').upper()
    direction=1 if sig=='BUY' else -1 if sig=='SELL' else (1 if float(row.get('change') or 0)>0 else -1 if float(row.get('change') or 0)<0 else 0)
    ma=(mc>0 if direction>0 else mc<0 if direction<0 else False)
    sa=(sc>0 if direction>0 else sc<0 if direction<0 else False)
    score=float(row.get('score') or 50)
    if sig in ('BUY','SELL'): score += 5 if ma else -5; score += 4 if sa else -4
    elif direction: score += 2 if ma else 0; score += 2 if sa else 0
    score=max(0,min(100,score))
    conf=row.get('confirmations') or {}; conf.update({'market':ma,'sector':sa})
    row.update({'score':round(score,1),'sector':sec,'sector_change':round(sc,3),'market_change':round(mc,3),
                'market_trend':ctx.get('market_trend','NEUTRAL'),'market_confirmed':ma,'sector_confirmed':sa,'confirmations':conf})
    if sig=='BUY' and not ma and not sa and score<72: row['signal']='WAIT'
    if sig=='SELL' and not ma and not sa and score>28: row['signal']='WAIT'
    return row

def _cdf(x): return .5*(1+math.erf(x/math.sqrt(2)))
def _pdf(x): return math.exp(-.5*x*x)/math.sqrt(2*math.pi)

def greeks(spot,strike,iv_pct,expiry,option_type):
    try:
        S=float(spot); K=float(strike); iv=float(iv_pct)/100
        if S<=0 or K<=0 or iv<=0 or not expiry: return {}
        d=datetime.strptime(str(expiry), '%d-%b-%Y'); T=max((d-datetime.now()).total_seconds()/86400/365,1/(365*1440))
        st=math.sqrt(T); r=RISK_FREE; d1=(math.log(S/K)+(r+.5*iv*iv)*T)/(iv*st); d2=d1-iv*st
        sign=1 if str(option_type).upper()=='CE' else -1
        delta=sign*_cdf(sign*d1); gamma=_pdf(d1)/(S*iv*st)
        theta=(-(S*_pdf(d1)*iv/(2*st))-sign*r*K*math.exp(-r*T)*_cdf(sign*d2))/365
        vega=S*_pdf(d1)*st/100
        return {'delta':round(delta,4),'gamma':round(gamma,6),'theta':round(theta,4),'vega':round(vega,4),'dte':round(T*365,3)}
    except Exception: return {}

def option_target_sl(ltp,spot,underlying_target,underlying_sl,delta,side):
    try:
        l=float(ltp); d=abs(float(delta or 0))
        up=max(0,float(underlying_target)-float(spot)); dn=max(0,float(spot)-float(underlying_sl))
        tgt=l+d*(up if side=='CALL' else dn); sl=l-d*(dn if side=='CALL' else up)
        return round(max(l*1.05,tgt),2),round(max(l*.70,sl),2)
    except Exception: return None,None

def _indicators(df):
    if df is None or len(df)<60: return None
    c={str(k).lower().strip():k for k in df.columns}; cc=c.get('close') or c.get('last')
    if cc is None: return None
    x=pd.DataFrame(index=df.index); x['close']=pd.to_numeric(df[cc],errors='coerce')
    for k in ('open','high','low','volume'):
        col=c.get(k); x[k]=pd.to_numeric(df[col],errors='coerce') if col else (x['close'] if k!='volume' else 1.0)
    x=x.replace([np.inf,-np.inf],np.nan).dropna(subset=['close'])
    if len(x)<60: return None
    close=x.close; ema9=close.ewm(span=9,adjust=False).mean(); ema21=close.ewm(span=21,adjust=False).mean()
    d=close.diff(); g=d.clip(lower=0).ewm(alpha=1/14,adjust=False).mean(); loss=(-d.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean()
    rsi=(100-100/(1+g/loss.replace(0,np.nan))).fillna(50); typical=(x.high+x.low+close)/3; vol=x.volume.clip(lower=0)
    vwap=typical.mul(vol).cumsum()/vol.cumsum().replace(0,np.nan)
    macd=close.ewm(span=12,adjust=False).mean()-close.ewm(span=26,adjust=False).mean(); macds=macd.ewm(span=9,adjust=False).mean()
    tr=pd.concat([x.high-x.low,(x.high-close.shift()).abs(),(x.low-close.shift()).abs()],axis=1).max(axis=1)
    atr=tr.ewm(alpha=1/14,adjust=False).mean(); vr=vol/vol.rolling(20,min_periods=5).mean().replace(0,np.nan)
    return x,ema9,ema21,rsi,vwap,macd,macds,atr,vr

def backtest_symbol(symbol,days=25,horizon=3):
    sym=norm(symbol); now=time.time(); hit=BACKTEST_CACHE.get(sym)
    if hit and now-hit['ts']<BACKTEST_TTL: return hit['data']
    try:
        df=historical.get_stock_historical_data(sym,datetime.now()-pd.Timedelta(days=days),datetime.now(),interval=5); z=_indicators(df)
        if z is None: return {'ok':False,'error':'Not enough historical 5-minute data'}
        x,e9,e21,rsi,vwap,macd,macds,atr,vr=z; close=x.close; outcomes=[]
        for i in range(50,len(x)-horizon):
            p=float(close.iloc[i]); rr=float(rsi.iloc[i]); vol=float(vr.iloc[i] or 0)
            bull=p>float(vwap.iloc[i]) and float(e9.iloc[i])>float(e21.iloc[i]) and rr>=52 and float(macd.iloc[i])>=float(macds.iloc[i]) and vol>=1.05
            bear=p<float(vwap.iloc[i]) and float(e9.iloc[i])<float(e21.iloc[i]) and rr<=48 and float(macd.iloc[i])<=float(macds.iloc[i]) and vol>=1.05
            if not (bull or bear): continue
            risk=max(p*.004,min(float(atr.iloc[i])*1.15,p*.018)); reward=max(p*.008,min(risk*2,p*.035)); fut=close.iloc[i+1:i+1+horizon]
            if bull: ht=bool((fut>=p+reward).any()); hs=bool((fut<=p-risk).any())
            else: ht=bool((fut<=p-reward).any()); hs=bool((fut>=p+risk).any())
            outcomes.append('AMBIGUOUS' if ht and hs else 'TARGET' if ht else 'SL' if hs else 'TIMEOUT')
        total=len(outcomes); wins=outcomes.count('TARGET'); losses=outcomes.count('SL'); decided=wins+losses
        data={'ok':True,'symbol':sym,'period_days':days,'timeframe':'5m','signals_tested':total,'target_hits':wins,'sl_hits':losses,
              'ambiguous':outcomes.count('AMBIGUOUS'),'timeouts':outcomes.count('TIMEOUT'),
              'historical_hit_rate':round(wins/decided*100,1) if decided else None,
              'note':'Underlying setup backtest, not option-contract win rate. Historical results do not guarantee future performance.'}
        BACKTEST_CACHE[sym]={'ts':now,'data':data}; return data
    except Exception as e: return {'ok':False,'error':str(e)[:180]}
