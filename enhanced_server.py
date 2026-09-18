import os, json, time, threading, math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests

import server

STATE = server.app_auto.STATE
STATE.setdefault('paper_positions', [])
STATE.setdefault('paper_history', [])
STATE.setdefault('signal_history', [])

PAPER_LOCK = threading.Lock()


def clean(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, dict): return {k: clean(x) for k, x in v.items()}
    if isinstance(v, list): return [clean(x) for x in v]
    return v


def market_state():
    d = datetime.now(ZoneInfo('Asia/Kolkata'))
    if d.weekday() >= 5: return 'CLOSED'
    m = d.hour * 60 + d.minute
    if m < 555: return 'PRE-OPEN'
    if m <= 930: return 'OPEN'
    return 'CLOSED'


def paper_mark():
    rows = {x.get('symbol'): x for x in STATE.get('rows', [])}
    with PAPER_LOCK:
        for p in STATE['paper_positions']:
            if p.get('status') != 'OPEN': continue
            r = rows.get(p.get('symbol'))
            if not r: continue
            ltp = float(r.get('price') or p.get('entry') or 0)
            p['ltp'] = ltp
            entry = float(p.get('entry', ltp)); qty = int(p.get('qty', 0))
            p['pnl'] = round((ltp-entry)*qty if p.get('side','LONG')=='LONG' else (entry-ltp)*qty, 2)
            target = float(p.get('target', 0)); sl = float(p.get('sl', 0))
            hit_t = target and (ltp >= target if p.get('side','LONG')=='LONG' else ltp <= target)
            hit_s = sl and (ltp <= sl if p.get('side','LONG')=='LONG' else ltp >= sl)
            if hit_t or hit_s:
                p['status'] = 'CLOSED'
                p['exit'] = ltp
                p['exit_reason'] = 'TARGET' if hit_t else 'STOP LOSS'
                p['closed_at'] = time.time()
                STATE['paper_history'].append(dict(p))
        STATE['paper_history'] = STATE['paper_history'][-500:]


def backtest(symbol, days=30, interval=5, capital=100000):
    symbol = str(symbol or '').upper().strip()
    days = max(5, min(int(days or 30), 120))
    interval = int(interval or 5)
    if interval not in (1,3,5,10,15,30,60): interval = 5
    end = datetime.now()
    df = server.historical.get_stock_historical_data(
        symbol, end-timedelta(days=days), end, interval=interval
    )
    if df is None or len(df) < 80:
        raise RuntimeError('Not enough historical candles for backtest')
    cols={str(c).lower().strip():c for c in df.columns}
    def col(*names):
        for n in names:
            if n in cols: return cols[n]
        return None
    oc,hc,lc,cc,vc=col('open'),col('high'),col('low'),col('close'),col('volume')
    if not all((oc,hc,lc,cc)): raise RuntimeError('Historical OHLC format unavailable')
    x=pd.DataFrame(index=df.index)
    x['open']=pd.to_numeric(df[oc],errors='coerce')
    x['high']=pd.to_numeric(df[hc],errors='coerce')
    x['low']=pd.to_numeric(df[lc],errors='coerce')
    x['close']=pd.to_numeric(df[cc],errors='coerce')
    x['volume']=pd.to_numeric(df[vc],errors='coerce') if vc else 1
    x=x.replace([math.inf,-math.inf],math.nan).dropna(subset=['close'])
    if len(x)<80: raise RuntimeError('Not enough clean candles')
    x['ema9']=x.close.ewm(span=9,adjust=False).mean()
    x['ema21']=x.close.ewm(span=21,adjust=False).mean()
    delta=x.close.diff()
    gain=delta.clip(lower=0).ewm(alpha=1/14,adjust=False).mean()
    loss=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean()
    x['rsi']=(100-100/(1+gain/loss.replace(0,math.nan))).fillna(50)
    x['macd']=x.close.ewm(span=12,adjust=False).mean()-x.close.ewm(span=26,adjust=False).mean()
    x['macd_sig']=x.macd.ewm(span=9,adjust=False).mean()
    x['vol_ratio']=x.volume/x.volume.rolling(20,min_periods=5).mean().replace(0,math.nan)
    x=x.dropna(subset=['ema9','ema21','rsi','macd','macd_sig'])
    cash=float(capital); pos=None; trades=[]; equity=[]
    risk_pct=.008; reward_pct=.016
    for ts,row in x.iterrows():
        price=float(row.close)
        if pos:
            hit_t=price>=pos['target'] if pos['side']=='LONG' else price<=pos['target']
            hit_s=price<=pos['sl'] if pos['side']=='LONG' else price>=pos['sl']
            if hit_t or hit_s:
                exitp=pos['target'] if hit_t else pos['sl']
                pnl=(exitp-pos['entry'])*pos['qty'] if pos['side']=='LONG' else (pos['entry']-exitp)*pos['qty']
                cash+=pnl
                trades.append({'time':str(ts),'side':pos['side'],'entry':round(pos['entry'],2),
                    'exit':round(exitp,2),'qty':pos['qty'],'pnl':round(pnl,2),
                    'reason':'TARGET' if hit_t else 'SL'})
                pos=None
        if not pos:
            bull=price>row.ema9>row.ema21 and row.rsi>=52 and row.macd>=row.macd_sig and row.vol_ratio>=.9
            bear=price<row.ema9<row.ema21 and row.rsi<=48 and row.macd<=row.macd_sig and row.vol_ratio>=.9
            if bull or bear:
                side='LONG' if bull else 'SHORT'
                qty=max(1,int(cash*.25/price))
                if qty>0:
                    entry=price
                    pos={'side':side,'entry':entry,'qty':qty,
                         'target':entry*(1+reward_pct if side=='LONG' else 1-reward_pct),
                         'sl':entry*(1-risk_pct if side=='LONG' else 1+risk_pct)}
        mark=cash
        if pos:
            mark += (price-pos['entry'])*pos['qty'] if pos['side']=='LONG' else (pos['entry']-price)*pos['qty']
        equity.append(mark)
    if pos:
        price=float(x.close.iloc[-1])
        pnl=(price-pos['entry'])*pos['qty'] if pos['side']=='LONG' else (pos['entry']-price)*pos['qty']
        cash+=pnl
        trades.append({'time':str(x.index[-1]),'side':pos['side'],'entry':round(pos['entry'],2),
            'exit':round(price,2),'qty':pos['qty'],'pnl':round(pnl,2),'reason':'END'})
    wins=sum(1 for t in trades if t['pnl']>0); total=sum(t['pnl'] for t in trades)
    peak=-1e99; maxdd=0
    for e in equity:
        peak=max(peak,e); maxdd=max(maxdd,peak-e)
    return clean({'ok':True,'symbol':symbol,'days':days,'interval':interval,
        'initial_capital':capital,'final_capital':round(cash,2),'net_pnl':round(cash-capital,2),
        'return_pct':round((cash/capital-1)*100,2),'trades':len(trades),'wins':wins,
        'losses':len(trades)-wins,'win_rate':round(wins/len(trades)*100,1) if trades else 0,
        'max_drawdown':round(maxdd,2),'sample_candles':len(x),'trade_log':trades[-100:]})


def groww_snapshot():
    token=os.environ.get('GROWW_ACCESS_TOKEN','').strip()
    if not token: return None
    syms=list(getattr(server,'NIFTY_SYMBOLS',[]))
    if not syms: return None
    headers={'Authorization':'Bearer '+token,'Accept':'application/json','X-API-VERSION':'1.0'}
    out={}
    for i in range(0,len(syms),50):
        ex=','.join('NSE_'+s for s in syms[i:i+50])
        r=requests.get('https://api.groww.in/v1/live-data/ltp',
            params={'segment':'CASH','exchange_symbols':ex},headers=headers,timeout=10)
        r.raise_for_status()
        out.update(r.json().get('payload',{}) or {})
    rows=[]
    for s in syms:
        p=out.get('NSE_'+s)
        if p is not None: rows.append({'symbol':s,'close':p,'changepct':0,'volume':0})
    return pd.DataFrame(rows) if rows else None


if os.environ.get('GROWW_ACCESS_TOKEN','').strip():
    original_snapshot=server.app_auto.get_snapshot
    def provider_snapshot():
        try:
            df=groww_snapshot()
            if df is not None and len(df): return df
        except Exception:
            pass
        return original_snapshot()
    server.app_auto.get_snapshot=provider_snapshot


class EnhancedHandler(server.NiftyHandler):
    def _json(self,obj,code=200): self.send_json(clean(obj),code)
    def do_GET(self):
        path,_,query=self.path.partition('?')
        if path=='/api/state':
            s=server.app_auto.load_settings()
            self._json({'rows':public_rows(),'ts':STATE.get('ts',0),
                'last_auto':STATE.get('last_auto',''),'error':STATE.get('last_error',''),
                'scanning':STATE.get('scanning',False),'progress':STATE.get('progress',0),
                'progress_text':STATE.get('progress_text',''),'scan_id':STATE.get('scan_id',0),
                'market':market_state(),'settings':{'auto':s.get('auto',True)}})
            return
        if path=='/api/paper':
            paper_mark(); self._json({'ok':True,'open':STATE['paper_positions'],'history':STATE['paper_history'][-100:]}); return
        if path=='/api/backtest':
            from urllib.parse import parse_qs
            q=parse_qs(query)
            symbol=(q.get('symbol',[''])[0] or '').upper()
            try:
                result=backtest(symbol,int(q.get('days',['30'])[0]),int(q.get('interval',['5'])[0]),float(q.get('capital',['100000'])[0]))
                self._json(result)
            except Exception as e: self._json({'ok':False,'error':str(e)[:200]},503)
            return
        if path=='/api/signals':
            self._json({'ok':True,'market':market_state(),'signals':STATE['signal_history'][-100:]}); return
        return super().do_GET()
    def do_POST(self):
        path=self.path.partition('?')[0]
        if path=='/api/paper':
            try:
                p=self.body()
                symbol=str(p.get('symbol','')).upper().strip()
                side=str(p.get('side','LONG')).upper()
                entry=float(p.get('entry')); qty=int(p.get('qty'))
                target=float(p.get('target')); sl=float(p.get('sl'))
                if side not in ('LONG','SHORT') or not symbol or qty<=0: raise ValueError('Invalid paper trade')
                item={'id':int(time.time()*1000),'symbol':symbol,'side':side,'entry':entry,
                    'ltp':entry,'qty':qty,'target':target,'sl':sl,'pnl':0,'status':'OPEN','created_at':time.time()}
                with PAPER_LOCK: STATE['paper_positions'].append(item)
                self._json({'ok':True,'position':item}); return
            except Exception as e: self._json({'ok':False,'error':str(e)},400); return
        if path=='/api/paper/close':
            try:
                p=self.body(); pid=int(p.get('id'))
                exitp=float(p.get('exit')) if p.get('exit') is not None else None
                with PAPER_LOCK:
                    item=next((x for x in STATE['paper_positions'] if x.get('id')==pid and x.get('status')=='OPEN'),None)
                    if not item: raise ValueError('Paper position not found')
                    ep=exitp if exitp is not None else item.get('ltp',item['entry'])
                    item['exit']=ep; item['status']='CLOSED'; item['exit_reason']='MANUAL'
                    item['closed_at']=time.time()
                    item['pnl']=round((ep-item['entry'])*item['qty'] if item['side']=='LONG' else (item['entry']-ep)*item['qty'],2)
                    STATE['paper_history'].append(dict(item))
                self._json({'ok':True,'position':item}); return
            except Exception as e: self._json({'ok':False,'error':str(e)},400); return
        return super().do_POST()


PANEL=r'''
<div class="panel" id="labPanel"><h2>🧪 ZERO-COST TEST LAB</h2>
<div class="small">Real-money order placement is OFF. Use this area for paper trading and historical testing before paying for a broker API.</div>
<div class="controls" style="margin-top:12px"><input id="btSymbol" placeholder="Symbol e.g. RELIANCE"><input id="btDays" type="number" value="30" min="5" max="120"><select id="btInterval"><option value="5">5m</option><option value="15">15m</option><option value="30">30m</option><option value="60">1h</option></select><input id="btCapital" type="number" value="100000"><button onclick="runBacktest()">BACKTEST</button><span id="btMsg">Ready</span></div>
<div id="btResult" class="small" style="margin-top:12px"></div>
<hr style="border:0;border-top:1px solid #edf0f4;margin:16px 0">
<div class="controls"><b>Paper Trade</b><input id="ppSymbol" placeholder="Symbol"><select id="ppSide"><option>LONG</option><option>SHORT</option></select><input id="ppEntry" type="number" placeholder="Entry"><input id="ppQty" type="number" placeholder="Qty"><input id="ppTarget" type="number" placeholder="Target"><input id="ppSL" type="number" placeholder="Stop Loss"><button onclick="addPaper()">PAPER BUY/SELL</button></div>
<div id="paperBox" class="small" style="margin-top:12px">Loading paper positions…</div></div>
'''
JS=r'''
<script>
async function runBacktest(){let s=document.getElementById('btSymbol').value.trim().toUpperCase();if(!s){document.getElementById('btMsg').textContent='Enter symbol';return}document.getElementById('btMsg').textContent='Running historical test…';try{let q=new URLSearchParams({symbol:s,days:document.getElementById('btDays').value,interval:document.getElementById('btInterval').value,capital:document.getElementById('btCapital').value});let r=await fetch('/api/backtest?'+q);let j=await r.json();if(!j.ok)throw Error(j.error);document.getElementById('btResult').innerHTML='<b>'+j.symbol+'</b> • '+j.sample_candles+' candles • Trades '+j.trades+' • Win rate '+j.win_rate+'% • Net P&L ₹'+Number(j.net_pnl).toFixed(2)+' • Return '+j.return_pct+'% • Max DD ₹'+Number(j.max_drawdown).toFixed(2);document.getElementById('btMsg').textContent='Done'}catch(e){document.getElementById('btMsg').textContent='Failed: '+e.message}}
async function addPaper(){let body={symbol:document.getElementById('ppSymbol').value.trim().toUpperCase(),side:document.getElementById('ppSide').value,entry:Number(document.getElementById('ppEntry').value),qty:Number(document.getElementById('ppQty').value),target:Number(document.getElementById('ppTarget').value),sl:Number(document.getElementById('ppSL').value)};let r=await fetch('/api/paper',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});let j=await r.json();if(!j.ok){alert(j.error);return}paperState()}
async function paperState(){try{let r=await fetch('/api/paper');let j=await r.json();let a=j.open||[];document.getElementById('paperBox').innerHTML=a.length?a.map(p=>p.symbol+' | '+p.side+' | Entry ₹'+p.entry+' | LTP ₹'+p.ltp+' | Qty '+p.qty+' | P&L ₹'+p.pnl+' | <b>'+p.status+'</b>').join('<br>'):'No open paper positions.'}catch(e){}}
paperState();setInterval(paperState,5000);
</script>
'''

html=server.app_auto.HTML
if 'id="labPanel"' not in html:
    html=html.replace('</body>',PANEL+JS+'</body>')
server.app_auto.HTML=html


def paper_loop():
    while True:
        try: paper_mark()
        except Exception: pass
        time.sleep(5)


def main():
    threading.Thread(target=server.app_auto.auto_loop,daemon=True).start()
    threading.Thread(target=server.refresh_nifty_cache,daemon=True).start()
    threading.Thread(target=paper_loop,daemon=True).start()
    server.start_background_scan(force=True)
    port=int(os.environ.get('PORT','10000'))
    server.app_auto.ThreadingHTTPServer(('0.0.0.0',port),EnhancedHandler).serve_forever()


if __name__=='__main__':
    main()
