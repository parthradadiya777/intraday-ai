import os
import threading
import time
import urllib.parse
import math
from datetime import datetime
import requests
import app_auto
import start
from nsemine import live, historical

# NIFTY50_DIRECT_PANEL

app_auto.STATE.setdefault('progress', 0)
app_auto.STATE.setdefault('progress_text', 'Ready')
app_auto.STATE.setdefault('scan_id', 0)

def snapshot_ai(row, max_volume):
    price = float(row.get('price', 0) or 0)
    change = float(row.get('change', 0) or 0)
    volume = float(row.get('volume', 0) or 0)
    volume_boost = min(8.0, (volume / max(max_volume, 1.0)) * 8.0)
    score = max(0.0, min(100.0, 50.0 + change * 1.6 + volume_boost))
    confidence = max(50.0, min(78.0, 50.0 + abs(change) * 1.3 + volume_boost * 0.35))
    if change >= 0.75 and score >= 65.0:
        signal = 'BUY'
    elif change <= -0.75 and score <= 35.0:
        signal = 'SELL'
    else:
        signal = 'WAIT'
    target = price * (1.012 if signal == 'BUY' else 0.988) if signal in ('BUY', 'SELL') else None
    sl = price * (0.99 if signal == 'BUY' else 1.01) if signal in ('BUY', 'SELL') else None
    return {
        'score': round(score, 1), 'signal': signal,
        'ai_confidence': round(confidence, 1), 'ai_model': 'SNAPSHOT-AI',
        'ai_probability': round(score / 100.0, 4),
        'ai_direction': 'UP' if change >= 0 else 'DOWN',
        'target': round(target, 2) if target else None,
        'sl': round(sl, 2) if sl else None,
        'momentum': round(change, 3), 'ai_validation': None,
        'ai_reason': 'Live NSE snapshot; selected stocks get 5-minute AI refinement',
    }

def scan():
    if app_auto.STATE.get('rows') and time.time() - app_auto.STATE.get('ts', 0) < 15:
        return app_auto.STATE.get('rows', [])
    with app_auto.STATE['lock']:
        if app_auto.STATE.get('scanning'):
            return app_auto.STATE.get('rows', [])
        app_auto.STATE['scanning'] = True
        app_auto.STATE['progress'] = 2
        app_auto.STATE['progress_text'] = 'Fetching fresh NSE market data…'
        try:
            df = app_auto.get_snapshot().copy()
            df['symbol'] = df['symbol'].astype(str).str.strip()
            df = df.drop_duplicates('symbol')
            # Product scope: only NIFTY 50 constituents.
            nifty_set = set(NIFTY_SYMBOLS)
            df = df[df['symbol'].map(lambda s: _clean_nifty_symbol(s) in nifty_set)].copy()
            rows = []
            for _, r in df.iterrows():
                try:
                    rows.append({
                        'symbol': str(r['symbol']).strip(),
                        'price': round(float(r['close']), 2),
                        'change': round(float(r['changepct']), 2),
                        'volume': int(float(r['volume'])) if r.get('volume') is not None else 0,
                        'score': 50.0, 'signal': 'WAIT', 'rsi': None,
                        'ema9': None, 'ema21': None, 'momentum': None,
                        'source': 'NSE', 'ai_confidence': None, 'ai_model': None,
                        'target': None, 'sl': None, 'ai_validation': None,
                        'ai_reason': None,
                    })
                except Exception:
                    continue
            if not rows:
                raise RuntimeError('NSE returned no usable equity rows')

            app_auto.STATE['progress'] = 20
            app_auto.STATE['progress_text'] = f'Scoring {len(rows)} NSE stocks…'
            max_volume = max((x['volume'] for x in rows), default=1)
            for x in rows:
                x.update(snapshot_ai(x, max_volume))
            app_auto.AI_SNAPSHOT = {x['symbol']: x for x in rows}

            candidates = sorted(
                rows,
                key=lambda x: (x['score'], abs(x['change']), x['volume']),
                reverse=True
            )[:15]
            app_auto.STATE['progress'] = 35
            app_auto.STATE['progress_text'] = f'Running 5-minute AI on {len(candidates)} strongest candidates…'

            from concurrent.futures import ThreadPoolExecutor, as_completed
            done = 0
            with ThreadPoolExecutor(max_workers=5) as ex:
                futures = {ex.submit(start.technical, x['symbol']): x for x in candidates}
                for future in as_completed(futures):
                    try:
                        data = future.result()
                        if data:
                            futures[future].update(data)
                    except Exception:
                        pass
                    done += 1
                    app_auto.STATE['progress'] = 35 + int(done / len(candidates) * 55)
                    app_auto.STATE['progress_text'] = f'AI refinement {done}/{len(candidates)}…'
            # Add option-chain context to the strongest F&O candidates.
            for x in candidates[:5]:
                try:
                    ob=option_bias(x['symbol'])
                    if ob:
                        x.update(ob)
                        x['score']=round(max(0.0,min(100.0,float(x.get('score',50))+float(ob.get('option_bias',0)))),1)
                        if x.get('signal')=='WAIT' and x['score']>=67:
                            x['signal']='BUY'
                        elif x.get('signal')=='WAIT' and x['score']<=33:
                            x['signal']='SELL'
                        x['ai_reason']=(x.get('ai_reason') or '')+' • Option-chain PCR included'
                except Exception:
                    pass

            rows.sort(key=lambda x: (x['score'], x['signal'] != 'WAIT', abs(x['change']), x['volume']), reverse=True)
            app_auto.STATE['rows'] = rows
            app_auto.STATE['ts'] = time.time()
            app_auto.STATE['last_error'] = ''
            app_auto.STATE['scan_id'] = int(app_auto.STATE.get('scan_id', 0)) + 1
            app_auto.STATE['progress'] = 100
            app_auto.STATE['progress_text'] = f'Complete • {len(rows)} NSE stocks scanned'
            return rows
        except Exception as e:
            app_auto.STATE['last_error'] = str(e)[:160]
            app_auto.STATE['progress_text'] = 'Scan failed — showing last available data'
            return app_auto.STATE.get('rows', [])
        finally:
            app_auto.STATE['scanning'] = False

app_auto.scan = scan

def start_background_scan(force=False):
    if app_auto.STATE.get('scanning'):
        return False
    if not force and app_auto.STATE.get('rows') and time.time() - app_auto.STATE.get('ts', 0) < 20:
        return False
    threading.Thread(target=scan, daemon=True).start()
    return True

app_auto.HTML = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<script src="https://unpkg.com/lightweight-charts@4.2.2/dist/lightweight-charts.standalone.production.js"></script>
<title>Intraday AI • NIFTY Live v5</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f4f6f8;color:#172033;font-family:Arial,sans-serif}
header{background:#101827;color:#fff;padding:22px 28px}h1{margin:0;font-size:28px}.sub{opacity:.72;margin-top:5px}
.status{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.chip{background:#1d2939;padding:8px 11px;border-radius:8px}
.wrap{max-width:1500px;margin:auto;padding:18px}.panel{background:#fff;border-radius:14px;padding:18px;margin-bottom:16px;box-shadow:0 2px 12px #0001}
.controls{display:flex;gap:10px;align-items:center;flex-wrap:wrap}input,button{padding:11px 13px;border:1px solid #ccd3dd;border-radius:8px;font-size:15px}
input{width:170px}button{background:#111827;color:#fff;font-weight:800;cursor:pointer}.hint,.small{font-size:12px;color:#687386}
.progress{height:8px;background:#e8edf2;border-radius:8px;overflow:hidden;margin-top:12px}.bar{height:100%;width:0;background:#1683ff;transition:width .25s}
.recommend{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.rec{border:1px solid #e2e7ed;border-radius:12px;padding:15px;cursor:pointer}.rec.buy{border-left:5px solid #0a8f45}.rec.sell{border-left:5px solid #d62828}
.symbol{font-size:20px;font-weight:800}.price{font-size:18px;margin-top:5px}.meta{font-size:13px;color:#687386;margin-top:7px}
.badge{display:inline-block;padding:5px 9px;border-radius:20px;background:#edf2f7;font-weight:800;font-size:12px}.green{color:#07833a}.red{color:#c62828}
.searchrow{display:flex;gap:10px;align-items:center;margin-bottom:12px}.search{width:280px}.searchinfo{color:#687386;font-size:12px}
.tablewrap{overflow:auto}table{width:100%;border-collapse:collapse}th,td{padding:10px 8px;border-bottom:1px solid #edf0f4;text-align:left;white-space:nowrap}th{font-size:11px;color:#697386}
tr.stockrow{cursor:pointer}tr.stockrow:hover{background:#f6f9fc}.fit{color:#07833a;font-weight:700}.notfit{color:#9a6b00}
.empty{padding:20px;text-align:center;color:#697386}
.modal{position:fixed;inset:0;background:#0008;display:none;align-items:center;justify-content:center;z-index:50;padding:15px}.modalbox{background:#fff;border-radius:16px;width:min(760px,100%);max-height:90vh;overflow:auto;padding:20px}
.modalhead{display:flex;justify-content:space-between;align-items:center}.close{background:#e9eef4;color:#172033}.detailgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:15px}.detail{background:#f6f8fa;border-radius:10px;padding:11px}.detail b{display:block;font-size:11px;color:#687386;margin-bottom:5px}.detail span{font-size:17px;font-weight:800}
.options-panel{margin-top:15px;border:1px solid #e3e8ee;border-radius:12px;padding:12px}.options-head{display:flex;justify-content:space-between;align-items:center;gap:10px}.options-head button{padding:7px 10px}.options-summary{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0}.opt-chip{background:#f5f7fa;border-radius:9px;padding:8px 10px;font-size:12px}.callcell{color:#07833a}.putcell{color:#c62828}@media(max-width:600px){.options-panel table{min-width:720px}}.spinner{display:inline-block;width:13px;height:13px;border:2px solid #cbd5e1;border-top-color:#111827;border-radius:50%;animation:spin .7s linear infinite;vertical-align:-2px}@keyframes spin{to{transform:rotate(360deg)}}
@media(max-width:900px){.recommend{grid-template-columns:1fr}.detailgrid{grid-template-columns:repeat(2,1fr)}.wrap{padding:10px}}
@media(max-width:600px){header{padding:15px 12px}h1{font-size:23px}.panel{padding:13px}.controls input,.controls button,.search{width:100%;min-height:44px}.searchrow{display:grid;grid-template-columns:1fr}.recommend{grid-template-columns:1fr}.detailgrid{grid-template-columns:1fr 1fr}table{min-width:1000px}th,td{font-size:12px;padding:9px 7px}}

.chart-toolbar{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:15px}
.chart-toolbar button{padding:7px 10px;font-size:12px}
#priceChart{width:100%;height:360px;margin-top:8px;border:1px solid #e2e7ed;border-radius:10px;overflow:hidden}
.chart-note{font-size:11px;color:#687386;margin:6px 0 12px}
@media(max-width:600px){#priceChart{height:300px}.chart-toolbar button{min-width:45px}}
</style></head>
<body>
<header><h1>Intraday AI</h1><div class="sub">NSE intraday AI scanner • multi-stock search • live recommendation • NIFTY Live v5</div>
<div class="status"><span class="chip" id="market">MARKET --</span><span class="chip" id="last">Last scan: --</span><span class="chip">AUTO SCAN ON</span></div></header>
<div class="wrap">
<div class="panel"><div class="controls"><b>Investment Budget ₹</b><input id="budget" type="number" value="5000" min="0" step="100">
<button onclick="scanNow()">SCAN NSE</button><span id="msg">Ready</span></div>
<div class="hint">Budget changes are applied immediately. Recommendations only show stocks that fit the current budget.</div>
<div class="progress"><div id="bar" class="bar"></div></div></div>

<div class="panel"><h2>🤖 AI RECOMMENDATION</h2><div id="recommendations" class="recommend"><div class="empty">Loading…</div></div></div>

<div class="panel"><h2>📊 AI STOCK LIST</h2>
<div class="searchrow"><input id="search" class="search" placeholder="🔍 Search any NSE stock / symbol">
<span id="searchinfo" class="searchinfo"></span></div>
<div class="small" style="margin-bottom:10px">Click any stock to open its detailed data. Budget-fit stocks are shown first.</div>
<div class="tablewrap"><table><thead><tr><th>Stock</th><th>Price</th><th>Change</th><th>AI Score</th><th>Confidence</th><th>Model</th><th>Signal</th><th>Qty</th><th>Investment</th><th>Target</th><th>Stop Loss</th><th>Budget</th></tr></thead><tbody id="rows"></tbody></table></div></div>
</div>

<div id="modal" class="modal" onclick="if(event.target===this)closeModal()"><div class="modalbox">
<div class="modalhead"><h2 id="dtitle">Stock</h2><button class="close" onclick="closeModal()">✕</button></div>
<div id="dsub" class="small"></div>
<div class="chart-toolbar">
  <b>📈 Price Chart</b>
  <button onclick="loadChart('1')">1m</button>
  <button onclick="loadChart('5')">5m</button>
  <button onclick="loadChart('15')">15m</button>
  <button onclick="loadChart('30')">30m</button>
  <button onclick="loadChart('60')">1h</button>
  <button onclick="loadChart('D')">1D</button>
</div>
<div id="chartStatus" class="small">Loading chart…</div>
<div id="priceChart"></div>
<div class="chart-note">Candlestick + volume + live LTP line • NSE market sync</div><div class="options-panel">
  <div class="options-head"><b>🟢 CALL / 🔴 PUT — Options Analysis</b><button onclick="loadOptions()">REFRESH</button></div>
  <div id="optionsStatus" class="small">Loading option chain…</div>
  <div id="optionsSummary" class="options-summary"></div>
  <div class="tablewrap"><table><thead><tr><th>Call LTP</th><th>Call OI</th><th>Call OI Δ</th><th>Strike</th><th>Put OI Δ</th><th>Put OI</th><th>Put LTP</th></tr></thead><tbody id="optionsRows"><tr><td colspan="7" class="empty">Loading…</td></tr></tbody></table></div>
</div>
<div id="details" class="detailgrid"></div>
<div id="dreason" class="hint" style="margin-top:15px"></div></div></div>

<script>
let DATA=[];let lastQuery='';const $=id=>document.getElementById(id);
const money=x=>x==null?'—':'₹'+Number(x).toFixed(2);const val=x=>x==null?'—':x;
function qty(x){let b=Math.max(0,Number($('budget').value||0));return x.price>0?Math.floor(b/x.price):0}
function market(){let d=new Date(),m=d.getHours()*60+d.getMinutes();$('market').textContent='MARKET '+(d.getDay()==0||d.getDay()==6?'CLOSED':m<555?'PRE-OPEN':m<=930?'OPEN':'CLOSED')}
setInterval(market,1000);market();
function card(x){let q=qty(x);return '<div class="rec '+(x.signal==='BUY'?'buy':'sell')+'" onclick="openStock(\''+encodeURIComponent(x.symbol)+'\')"><div class="symbol">'+x.symbol+'</div><div class="price">'+money(x.price)+' <span class="badge '+(x.signal==='BUY'?'green':'red')+'">'+x.signal+'</span></div><div class="meta">AI Score <b>'+val(x.score)+'</b> · Confidence <b>'+val(x.ai_confidence)+'%</b></div><div class="meta">Model: <b>'+val(x.ai_model)+'</b></div><div class="meta">Qty <b>'+q+'</b> · Investment <b>'+money(q*x.price)+'</b></div><div class="meta">Target <b>'+money(x.target)+'</b> · SL <b>'+money(x.sl)+'</b></div></div>'}
function render(){
 let q=lastQuery.trim().toUpperCase(),budget=Number($('budget').value||0);
 let filtered=DATA.filter(x=>!q||String(x.symbol).toUpperCase().includes(q));
 let affordable=filtered.filter(x=>qty(x)>0);
 let rec=affordable.filter(x=>x.signal==='BUY'||x.signal==='SELL').sort((a,b)=>Number(b.score)-Number(a.score)).slice(0,3);
 $('recommendations').innerHTML=rec.length?rec.map(card).join(''):'<div class="empty">No budget-fit BUY/SELL recommendation right now.</div>';
 let sorted=filtered.slice().sort((a,b)=>{let af=qty(a)>0,bf=qty(b)>0;if(af!==bf)return bf-af;return Number(b.score)-Number(a.score)});
 $('searchinfo').textContent=(q?('Found '+filtered.length+' stock(s)'):('Showing '+Math.min(sorted.length,100)+' of '+DATA.length+' stocks'))+' • Budget ₹'+budget.toLocaleString('en-IN');
 $('rows').innerHTML=sorted.slice(0,100).map(x=>{let qy=qty(x),fit=qy>0;return '<tr class="stockrow" onclick="openStock(\''+encodeURIComponent(x.symbol)+'\')"><td><b>'+x.symbol+'</b></td><td>'+money(x.price)+'</td><td>'+val(x.change)+'%</td><td><b>'+val(x.score)+'</b></td><td>'+val(x.ai_confidence)+'%</td><td>'+val(x.ai_model)+'</td><td><span class="badge">'+x.signal+'</span></td><td>'+qy+'</td><td>'+money(qy*x.price)+'</td><td>'+money(x.target)+'</td><td>'+money(x.sl)+'</td><td class="'+(fit?'fit':'notfit')+'">'+(fit?'✓ FIT':'—')+'</td></tr>'}).join('')||'<tr><td colspan="12" class="empty">No matching stock.</td></tr>';
}
function renderState(j){
 DATA=j.rows||DATA;$('last').textContent='Last scan: '+(j.ts?new Date(j.ts*1000).toLocaleTimeString('en-IN'):'--');
 $('msg').innerHTML=j.scanning?'<span class="spinner"></span> '+(j.progress_text||'Scanning…'):(j.error?'Last scan had an error':'Live NSE data');
 $('bar').style.width=(j.progress||0)+'%';render();
}
async function state(){try{let r=await fetch('/api/state');renderState(await r.json())}catch(e){$('msg').textContent='Connection error'}}
async function scanNow(){if(window.scanning)return;window.scanning=true;$('msg').innerHTML='<span class="spinner"></span> Starting fresh NSE scan…';try{await fetch('/api/scan?start=1')}catch(e){}let timer=setInterval(async()=>{await state();let r=await fetch('/api/state');let j=await r.json();if(!j.scanning){clearInterval(timer);window.scanning=false;renderState(j)}},700)}
async function openStock(sym){sym=decodeURIComponent(sym);activeChartSymbol=sym;$('modal').style.display='flex';$('dtitle').textContent=sym;$('dsub').textContent='Loading latest stock data…';$('details').innerHTML='<div class="empty">Fetching…</div>';loadChart('5');loadOptions();try{let r=await fetch('/api/stock?symbol='+encodeURIComponent(sym));let j=await r.json();if(!j.ok)throw Error(j.error||'Failed');let x=j.data;$('dsub').textContent='NSE • '+(j.refined?'5-minute AI refined':'snapshot data');let items=[['Price',money(x.price)],['Change',val(x.change)+'%'],['AI Score',val(x.score)],['Signal',val(x.signal)],['Confidence',val(x.ai_confidence)+'%'],['Model',val(x.ai_model)],['RSI',val(x.rsi)],['EMA 9',money(x.ema9)],['EMA 21',money(x.ema21)],['VWAP',money(x.vwap)],['MACD',val(x.macd)],['MACD Signal',val(x.macd_signal)],['ADX',val(x.adx)],['Relative Volume',val(x.relative_volume)+'x'],['ATR',money(x.atr)],['Momentum',val(x.momentum)+'%'],['Volume',Number(x.volume||0).toLocaleString('en-IN')],['Target',money(x.target)],['Stop Loss',money(x.sl)]];$('details').innerHTML=items.map(a=>'<div class="detail"><b>'+a[0]+'</b><span>'+a[1]+'</span></div>').join('');$('dreason').textContent=x.ai_reason||'No additional AI explanation available.'}catch(e){$('details').innerHTML='<div class="empty">'+e.message+'</div>';$('dsub').textContent='Unable to load stock details'}}
function closeModal(){$('modal').style.display='none';if(chartRefreshTimer){clearInterval(chartRefreshTimer);chartRefreshTimer=null}if(activeChart){try{activeChart.remove()}catch(e){}activeChart=null;activeCandleSeries=null;activeVolumeSeries=null;activeLineSeries=null;liveLinePoints=[]}}
$('budget').addEventListener('input',()=>render());$('search').addEventListener('input',()=>{lastQuery=$('search').value;render()});
state();setInterval(state,1500);

let activeChartSymbol='', activeChart=null, activeCandleSeries=null, activeVolumeSeries=null, activeLineSeries=null;
let liveLinePoints=[];
let chartRefreshTimer=null, chartInterval='5';
async function loadChart(interval='5'){
  if(!activeChartSymbol)return;
  chartInterval=interval;
  $('chartStatus').innerHTML='<span class="spinner"></span> Syncing '+interval+'m candles with NSE…';
  try{
    const r=await fetch('/api/chart?symbol='+encodeURIComponent(activeChartSymbol)+'&interval='+encodeURIComponent(interval),{cache:'no-store'});
    const j=await r.json();
    if(!j.ok)throw Error(j.error||'Chart unavailable');
    const el=$('priceChart');

    // Reuse the same chart when possible; only rebuild if the container changed.
    if(!activeChart){
      activeChart=LightweightCharts.createChart(el,{
        width:el.clientWidth,height:el.clientHeight,
        layout:{background:{color:'#ffffff'},textColor:'#687386'},
        grid:{vertLines:{color:'#edf0f4'},horzLines:{color:'#edf0f4'}},
        rightPriceScale:{borderColor:'#dce2e8'},
        timeScale:{borderColor:'#dce2e8',timeVisible:true,secondsVisible:false},
        crosshair:{mode:1}
      });
      activeCandleSeries=activeChart.addCandlestickSeries({
        upColor:'#16a34a',downColor:'#dc2626',borderVisible:false,
        wickUpColor:'#16a34a',wickDownColor:'#dc2626'
      });
      activeVolumeSeries=activeChart.addHistogramSeries({
        priceFormat:{type:'volume'},priceScaleId:'volume',
        scaleMargins:{top:0.82,bottom:0}
      });
      activeLineSeries=activeChart.addLineSeries({
        lineWidth:2, priceLineVisible:false, lastValueVisible:true
      });
      window.addEventListener('resize',()=>{if(activeChart)activeChart.resize(el.clientWidth,el.clientHeight)});
    }

    const baseLine=j.rows.map(x=>({time:x.time,value:x.close}));
    activeLineSeries.setData(baseLine);
    activeCandleSeries.setData(j.rows.map(x=>({time:x.time,open:x.open,high:x.high,low:x.low,close:x.close})));
    activeVolumeSeries.setData(j.rows.map(x=>({time:x.time,value:x.volume,color:x.close>=x.open?'#86efac':'#fca5a5'})));
    activeChart.timeScale().fitContent();

    // Add the newest live quote as a continuously updating price line.
    try{
      const qr=await fetch('/api/live_quote?symbol='+encodeURIComponent(activeChartSymbol),{cache:'no-store'});
      const qj=await qr.json();
      if(qj.ok && qj.price!=null){
        const t=Math.floor(Date.now()/1000);
        const last=baseLine.length?baseLine[baseLine.length-1]:null;
        const qt=last && t<=last.time ? last.time+1 : t;
        activeLineSeries.update({time:qt,value:Number(qj.price)});
        $('chartStatus').textContent=(j.rows.length)+' candles • '+interval+'m • LIVE LTP '+money(qj.price);
      }
    }catch(e){}

    const last=j.rows[j.rows.length-1];
    const lastTime=last?new Date(last.time*1000):null;
    const session=lastTime?lastTime.toLocaleDateString('en-IN',{day:'2-digit',month:'short'}):'—';
    $('chartStatus').textContent=(j.rows.length)+' candles • '+interval+'m • Session '+session+' • NSE sync';
  }catch(e){
    $('chartStatus').textContent='Chart error: '+e.message;
  }
}

function startChartLiveRefresh(){
  if(chartRefreshTimer)clearInterval(chartRefreshTimer);
  chartRefreshTimer=setInterval(()=>{
    if($('modal').style.display==='flex' && activeChartSymbol) loadChart(chartInterval);
  },5000);
}
async function loadOptions(){
  if(!activeChartSymbol)return;
  $('optionsStatus').innerHTML='<span class="spinner"></span> Loading live CALL/PUT data…';
  try{
    const r=await fetch('/api/options?symbol='+encodeURIComponent(activeChartSymbol),{cache:'no-store'});
    const j=await r.json();
    if(!j.ok)throw Error(j.error||'Options unavailable');
    $('optionsStatus').textContent='Expiry '+(j.expiry||'—')+' • ATM '+j.atm+' • NSE option chain';
    const pcr=j.pcr==null?'—':Number(j.pcr).toFixed(2);
    $('optionsSummary').innerHTML='<span class="opt-chip">Spot <b>'+money(j.spot)+'</b></span><span class="opt-chip">ATM <b>'+j.atm+'</b></span><span class="opt-chip">PCR <b>'+pcr+'</b></span><span class="opt-chip">Option data feeds prediction</span>';
    $('optionsRows').innerHTML=j.rows.map(x=>'<tr><td class="callcell">'+val(x.call.ltp)+'</td><td>'+val(x.call.oi)+'</td><td>'+val(x.call.oi_change)+'</td><td><b>'+x.strike+'</b></td><td>'+val(x.put.oi_change)+'</td><td>'+val(x.put.oi)+'</td><td class="putcell">'+val(x.put.ltp)+'</td></tr>').join('');
  }catch(e){
    $('optionsStatus').textContent='Options unavailable: '+e.message;
    $('optionsRows').innerHTML='<tr><td colspan="7" class="empty">No option-chain data for this stock.</td></tr>';
  }
}
startChartLiveRefresh();
</script></body></html>'''


# NIFTY 50 background cache: never block browser requests on NSE.
NIFTY_CACHE = {'rows': [], 'index': {}, 'breadth': {'advance': 0, 'decline': 0, 'unchanged': 0}, 'error': '', 'ts': 0}
NIFTY_LOCK = threading.Lock()
NIFTY_SYMBOLS = [
    'ADANIENT','ADANIPORTS','APOLLOHOSP','ASIANPAINT','AXISBANK','BAJAJ-AUTO',
    'BAJAJFINSV','BAJFINANCE','BEL','BHARTIARTL','CIPLA','COALINDIA','DRREDDY',
    'EICHERMOT','ETERNAL','GRASIM','HCLTECH','HDFCBANK','HDFCLIFE','HEROMOTOCO',
    'HINDALCO','HINDUNILVR','ICICIBANK','INDUSINDBK','INFY','ITC','JIOFIN','JSWSTEEL',
    'KOTAKBANK','LT','M&M','MARUTI','NESTLEIND','NTPC','ONGC','POWERGRID','RELIANCE',
    'SBILIFE','SBIN','SHRIRAMFIN','SUNPHARMA','TATACONSUM','TATAMOTORS','TATASTEEL',
    'TCS','TECHM','TITAN','TRENT','ULTRACEMCO','WIPRO'
]
NIFTY_CACHE['rows'] = [{'symbol': s, 'price': None, 'change': None, 'weightage': None, 'volume': None, 'turnover': None} for s in NIFTY_SYMBOLS]
NIFTY_CACHE['error'] = 'Live NIFTY 50 quotes are not published yet; showing all 50 constituents'

def _clean_nifty_symbol(value):
    """Normalize NSE feed symbols so suffix/prefix variants still match NIFTY."""
    s = str(value or '').strip().upper()
    for prefix in ('NSE:', 'NSE_'):
        if s.startswith(prefix):
            s = s[len(prefix):]
    for suffix in ('-EQ', '.NS'):
        if s.endswith(suffix):
            s = s[:-len(suffix)]
    return s


def refresh_nifty_cache():
    while True:
        try:
            rows_by_symbol = {}

            # FIRST: use the main scanner's already-fresh NSE snapshot.
            # This must be published to the NIFTY cache BEFORE any slower
            # dedicated NIFTY/index request, otherwise the whole NIFTY panel
            # stays empty while that request is waiting.
            try:
                current = app_auto.STATE.get('rows', []) or []
                for r in current:
                    sym = _clean_nifty_symbol(r.get('symbol'))
                    if sym in NIFTY_SYMBOLS:
                        rows_by_symbol[sym] = {
                            'symbol': sym,
                            'price': r.get('price'),
                            'change': r.get('change'),
                            'weightage': None,
                            'volume': r.get('volume'),
                            'turnover': r.get('turnover')
                        }
            except Exception:
                pass

            # If the scanner has not produced rows yet, fetch the normal NSE
            # equity snapshot as a background fallback.
            if len(rows_by_symbol) < 10:
                try:
                    df = app_auto.get_snapshot()
                except Exception:
                    df = None
                if df is not None and len(df):
                    for _, r in df.iterrows():
                        sym = _clean_nifty_symbol(r.get('symbol'))
                        if sym in NIFTY_SYMBOLS:
                            rows_by_symbol[sym] = {
                                'symbol': sym,
                                'price': r.get('close', r.get('ltp')),
                                'change': r.get('changepct'),
                                'weightage': None,
                                'volume': r.get('volume'),
                                'turnover': r.get('traded_value')
                            }

            # Last-resort direct quote fan-out. This keeps the NIFTY panel
            # populated even when the bulk constituent endpoint is unavailable.
            if len(rows_by_symbol) < 25:
                try:
                    from concurrent.futures import ThreadPoolExecutor, as_completed
                    def one_quote(sym):
                        try:
                            q=live.get_stock_live_quotes(sym)
                            if not q: return None
                            return sym, {'symbol':sym,'price':q.get('close'),'change':q.get('changepct'),
                                         'weightage':None,'volume':q.get('volume'),'turnover':q.get('traded_value')}
                        except Exception:
                            return None
                    with ThreadPoolExecutor(max_workers=8) as ex:
                        futures=[ex.submit(one_quote,s) for s in NIFTY_SYMBOLS if s not in rows_by_symbol]
                        for f in as_completed(futures):
                            item=f.result()
                            if item and item[1].get('price') is not None:
                                rows_by_symbol[item[0]]=item[1]
                except Exception:
                    pass

            rows = [rows_by_symbol.get(s, {
                'symbol': s, 'price': None, 'change': None,
                'weightage': None, 'volume': None, 'turnover': None
            }) for s in NIFTY_SYMBOLS]

            valid = [r for r in rows if r.get('price') is not None]
            breadth = {
                'advance': sum(1 for r in valid if float(r.get('change') or 0) > 0),
                'decline': sum(1 for r in valid if float(r.get('change') or 0) < 0),
                'unchanged': sum(1 for r in valid if float(r.get('change') or 0) == 0)
            }

            # Publish stock rows immediately. Slow index/NIFTY enrichment must
            # never prevent the 50 stocks from appearing in the browser.
            with NIFTY_LOCK:
                NIFTY_CACHE.update({
                    'rows': rows[:50],
                    'breadth': breadth,
                    'error': '' if valid else 'Waiting for NSE snapshot',
                    'ts': time.time()
                })

            # Index quote is optional enrichment. Even if this call is slow,
            # the stock table above has already been published.
            try:
                idx = live.get_index_live_price('NIFTY 50') or {}
                if idx:
                    with NIFTY_LOCK:
                        NIFTY_CACHE['index'] = idx
                        NIFTY_CACHE['ts'] = time.time()
            except Exception:
                pass

        except Exception as e:
            with NIFTY_LOCK:
                NIFTY_CACHE['error'] = str(e)[:180]
        time.sleep(5)

# NIFTY50 UI injection
_NIFTY50_PANEL = '''
<div class="panel nifty-panel"><h2>🇮🇳 NIFTY 50</h2><div id="niftyIndex" class="nifty-index"><span class="spinner"></span> Preparing live index…</div><div id="niftyBreadth" class="nifty-breadth"><div class="breadth-box"><b>—</b><span>ADVANCE</span></div><div class="breadth-box"><b>—</b><span>DECLINE</span></div><div class="breadth-box"><b>—</b><span>UNCHANGED</span></div></div><div id="niftyMovers" class="nifty-movers"></div><div class="searchrow"><input id="niftySearch" class="search" placeholder="🔍 Search NIFTY 50 stock"><span id="niftyInfo" class="searchinfo"></span></div><div class="tablewrap nifty-table"><table><thead><tr><th>Stock</th><th>Price</th><th>Change</th><th>Weight</th><th>Volume</th><th>Turnover</th></tr></thead><tbody id="niftyRows"><tr><td colspan="6" class="empty"><span class="spinner"></span> Loading 50 NIFTY stocks…</td></tr></tbody></table></div></div>
'''
_NIFTY50_SCRIPT = '''<style>.nifty-breadth{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px}.breadth-box{background:#f6f8fa;border-radius:10px;padding:9px;text-align:center}.breadth-box b{display:block;font-size:18px}.breadth-box span{font-size:11px;color:#687386}.nifty-movers{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px}.mover-box{background:#f8fafc;border:1px solid #e7ebf0;border-radius:10px;padding:10px}.mover-title{font-size:11px;color:#687386;margin-bottom:6px;font-weight:800}.mover-item{display:flex;justify-content:space-between;font-size:12px;padding:3px 0}.nifty-index{display:flex;gap:18px;align-items:center;flex-wrap:wrap;background:#f6f8fa;border-radius:12px;padding:14px;margin-bottom:14px}.nifty-main{font-size:24px;font-weight:900}.nifty-change{font-size:18px;font-weight:800}.nifty-meta{font-size:12px;color:#687386}.nifty-panel{margin:16px 18px 0}.nifty-table{overflow:visible}.nifty-table table{min-width:0}.nifty-table th,.nifty-table td{padding:9px 7px}@media(max-width:600px){.nifty-panel{margin:10px}.nifty-movers{grid-template-columns:1fr}.nifty-table table{display:block}.nifty-table thead{display:none}.nifty-table tbody{display:grid;grid-template-columns:1fr 1fr;gap:8px}.nifty-table tr{display:grid;grid-template-columns:1fr auto;gap:2px 8px;border:1px solid #e5e9ef;border-radius:10px;padding:9px;background:#fff}.nifty-table td{border:0;padding:2px 0;white-space:normal}.nifty-table td:nth-child(1){font-size:14px}.nifty-table td:nth-child(2){text-align:right}.nifty-table td:nth-child(3){text-align:right}.nifty-table td:nth-child(4),.nifty-table td:nth-child(5),.nifty-table td:nth-child(6){font-size:11px;color:#687386}.nifty-table td:nth-child(4)::before{content:'Wt ';}.nifty-table td:nth-child(5)::before{content:'Vol ';}.nifty-table td:nth-child(6)::before{content:'Turn ';}}@media(max-width:420px){.nifty-table tbody{grid-template-columns:1fr}}</style><script>
let NIFTY50=[];const n$=id=>document.getElementById(id);function niftyMoney(x){return x==null?'—':'₹'+Number(x).toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2});}
function niftyRender(){
 const rawQ=(n$('niftySearch').value||'').trim().toUpperCase();
 const q=(rawQ==='NIFTY50'||rawQ==='NIFTY 50')?'':rawQ;
 const rows=NIFTY50.filter(x=>!q||x.symbol.toUpperCase().includes(q));
 n$('niftyInfo').textContent='Showing '+rows.length+' of '+NIFTY50.length+' NIFTY 50 stocks';
 n$('niftyRows').innerHTML=rows.map(x=>{const ch=x.change==null?null:Number(x.change);const cls=ch!=null?(ch>=0?'green':'red'):'';return '<tr><td><b>'+x.symbol+'</b></td><td>'+niftyMoney(x.price)+'</td><td class="'+cls+'">'+(ch==null?'—':(ch>=0?'+':'')+ch.toFixed(2)+'%')+'</td><td>'+(x.weightage==null?'—':Number(x.weightage).toFixed(2)+'%')+'</td><td>'+(x.volume==null?'—':Number(x.volume).toLocaleString('en-IN'))+'</td><td>'+(x.turnover==null?'—':Number(x.turnover).toLocaleString('en-IN'))+'</td></tr>';}).join('')||'<tr><td colspan="6" class="empty">No matching NIFTY 50 stock.</td></tr>';
}
function niftyMovers(){
 const v=NIFTY50.filter(x=>x.price!=null&&x.change!=null);
 const g=v.slice().sort((a,b)=>Number(b.change)-Number(a.change)).slice(0,5);
 const l=v.slice().sort((a,b)=>Number(a.change)-Number(b.change)).slice(0,5);
 n$('niftyMovers').innerHTML='<div class="mover-box"><div class="mover-title">TOP GAINERS</div>'+g.map(x=>'<div class="mover-item"><b>'+x.symbol+'</b><span class="green">+'+Number(x.change).toFixed(2)+'%</span></div>').join('')+'</div><div class="mover-box"><div class="mover-title">TOP LOSERS</div>'+l.map(x=>'<div class="mover-item"><b>'+x.symbol+'</b><span class="red">'+Number(x.change).toFixed(2)+'%</span></div>').join('')+'</div>';
}
async function loadNifty50(){
 try{
  const [ir,cr]=await Promise.all([fetch('/api/nifty50/index?t='+Date.now(),{cache:'no-store'}),fetch('/api/nifty50?t='+Date.now(),{cache:'no-store'})]);
  const ij=await ir.json(),cj=await cr.json();
  if(cj.rows&&cj.rows.length){NIFTY50=cj.rows.slice(0,50);niftyRender();niftyMovers();}
  const b=cj.breadth||ij.breadth||{};
  n$('niftyBreadth').innerHTML='<div class="breadth-box"><b class="green">'+(b.advance||0)+'</b><span>ADVANCE</span></div><div class="breadth-box"><b class="red">'+(b.decline||0)+'</b><span>DECLINE</span></div><div class="breadth-box"><b>'+(b.unchanged||0)+'</b><span>UNCHANGED</span></div>';
  if(ij.ok&&ij.data){
   const d=ij.data,ch=Number(d.changepct??d.change??0);
   n$('niftyIndex').innerHTML='<span class="nifty-main">NIFTY 50 '+niftyMoney(d.close??d.price)+'</span><span class="nifty-change '+(ch>=0?'green':'red')+'">'+(ch>=0?'+':'')+ch.toFixed(2)+'%</span><span class="nifty-meta">O '+niftyMoney(d.open)+' · H '+niftyMoney(d.high)+' · L '+niftyMoney(d.low)+' · Prev '+niftyMoney(d.previous_close)+'</span>';
  }else{
   n$('niftyIndex').innerHTML='<span class="small"><span class="spinner"></span> Waiting for live NIFTY index…</span>';
  }
 }catch(e){
  n$('niftyInfo').textContent=NIFTY50.length?'Live reconnecting • '+NIFTY50.length+' cached stocks':'Waiting for NSE snapshot…';
 }
}
n$('niftySearch').addEventListener('input',niftyRender);loadNifty50();setInterval(loadNifty50,5000);</script>'''
app_auto.HTML = app_auto.HTML.replace('<div class="wrap">', _NIFTY50_PANEL + '<div class="wrap">' + _NIFTY50_SCRIPT)

def _clean_json_value(v):
    if isinstance(v, (datetime,)):
        return v.isoformat()
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, dict):
        return {k: _clean_json_value(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_clean_json_value(x) for x in v]
    if isinstance(v, tuple):
        return [_clean_json_value(x) for x in v]
    return v

def _nse_option_chain(symbol):
    s = str(symbol or '').upper().strip()
    if not s:
        return {'ok': False, 'error': 'Missing symbol'}
    is_index = s in ('NIFTY', 'NIFTY 50', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY')
    api = 'https://www.nseindia.com/api/option-chain-indices' if is_index else 'https://www.nseindia.com/api/option-chain-equities'
    api_symbol = 'NIFTY' if s == 'NIFTY 50' else s
    headers = {'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/153 Safari/537.36',
               'Accept':'application/json,text/plain,*/*','Referer':'https://www.nseindia.com/option-chain'}
    try:
        sess=requests.Session()
        sess.headers.update(headers)
        sess.get('https://www.nseindia.com',timeout=8)
        r=sess.get(api,params={'symbol':api_symbol},timeout=10)
        r.raise_for_status()
        raw=r.json()
        records=raw.get('records',{}) if isinstance(raw,dict) else {}
        expiries=records.get('expiryDates') or []
        expiry=expiries[0] if expiries else None
        data=[]
        for x in records.get('data',[]) or []:
            if expiry and x.get('expiryDate') != expiry: continue
            strike=x.get('strikePrice')
            if strike is None: continue
            ce=x.get('CE') or {}
            pe=x.get('PE') or {}
            data.append({'strike':strike,'expiry':x.get('expiryDate'),
                         'call':{'ltp':ce.get('lastPrice'),'oi':ce.get('openInterest'),'oi_change':ce.get('changeinOpenInterest'),
                                 'volume':ce.get('totalTradedVolume'),'iv':ce.get('impliedVolatility'),'bid':ce.get('bidprice'),'ask':ce.get('askPrice')},
                         'put':{'ltp':pe.get('lastPrice'),'oi':pe.get('openInterest'),'oi_change':pe.get('changeinOpenInterest'),
                                'volume':pe.get('totalTradedVolume'),'iv':pe.get('impliedVolatility'),'bid':pe.get('bidprice'),'ask':pe.get('askPrice')}})
        spot=records.get('underlyingValue')
        if spot is None:
            q=live.get_index_live_price('NIFTY 50') if is_index else live.get_stock_live_quotes(api_symbol)
            spot=(q or {}).get('close')
        if not data: return {'ok':False,'error':'No option-chain data available'}
        data.sort(key=lambda x: float(x['strike']))
        atm=min(data,key=lambda x:abs(float(x['strike'])-float(spot))) if spot is not None else data[len(data)//2]
        ai=float(atm['strike'])
        selected=sorted(data,key=lambda x:abs(float(x['strike'])-ai))[:11]
        call_oi=sum(float(x['call']['oi'] or 0) for x in selected)
        put_oi=sum(float(x['put']['oi'] or 0) for x in selected)
        pcr=(put_oi/call_oi) if call_oi else None
        return _clean_json_value({'ok':True,'symbol':api_symbol,'spot':spot,'expiry':expiry,'atm':atm['strike'],
                                  'pcr':pcr,'rows':selected,'source':'NSE option chain'})
    except Exception as e:
        return {'ok':False,'error':str(e)[:180]}

OPTION_CACHE={}
OPTION_CACHE_LOCK=threading.Lock()
OPTION_TTL=60

def option_bias(symbol):
    now=time.time()
    with OPTION_CACHE_LOCK:
        hit=OPTION_CACHE.get(symbol)
        if hit and now-hit['ts']<OPTION_TTL:
            return hit['data']
    try:
        j=_nse_option_chain(symbol)
        if not j.get('ok'):
            return None
        pcr=j.get('pcr')
        bias=0.0
        if pcr is not None:
            if pcr >= 1.20: bias=5.0
            elif pcr >= 1.05: bias=2.5
            elif pcr <= 0.80: bias=-5.0
            elif pcr <= 0.95: bias=-2.5
        data={'option_pcr':pcr,'option_bias':bias,'option_expiry':j.get('expiry'),'option_atm':j.get('atm')}
        with OPTION_CACHE_LOCK: OPTION_CACHE[symbol]={'ts':now,'data':data}
        return data
    except Exception:
        return None

class FastHandler(app_auto.Handler):
    def send_json(self, obj, code=200):
        return super().send_json(_clean_json_value(obj), code)

    def do_GET(self):
        path, _, query = self.path.partition('?')
        if path == '/api/scan':
            start_background_scan(force=True); self.send_json({'ok': True, 'started': True}); return
        if path == '/api/state':
            s=app_auto.load_settings()
            self.send_json({'rows':app_auto.STATE.get('rows',[]),'ts':app_auto.STATE.get('ts',0),
                'last_auto':app_auto.STATE.get('last_auto',''),'error':app_auto.STATE.get('last_error',''),
                'scanning':app_auto.STATE.get('scanning',False),'progress':app_auto.STATE.get('progress',0),
                'progress_text':app_auto.STATE.get('progress_text',''),'scan_id':app_auto.STATE.get('scan_id',0),
                'settings':{'auto':s.get('auto',True)}}); return

        if path == '/api/chart':
            qs = urllib.parse.parse_qs(query)
            symbol = (qs.get('symbol', [''])[0] or '').upper().strip()
            interval_raw = (qs.get('interval', ['5'])[0] or '5').upper().strip()
            if not symbol:
                self.send_json({'ok': False, 'error': 'Missing symbol'}, 400); return
            try:
                if interval_raw in ('D','W','M'):
                    interval = interval_raw
                    days = 500 if interval == 'D' else 1500
                    df = historical.get_stock_historical_data(
                        symbol,
                        __import__('datetime').datetime.now() - __import__('datetime').timedelta(days=days),
                        __import__('datetime').datetime.now(),
                        interval=interval
                    )
                else:
                    interval = int(interval_raw)
                    if interval not in (1,3,5,10,15,30,60):
                        interval = 5

                    # Build the intraday chart from BOTH sources:
                    # 1) historical candles provide the full session history;
                    # 2) live NSE candles overlay the latest/current candle.
                    # This avoids the old "1 candle" chart when NSE's live endpoint
                    # returns only the currently forming candle.
                    import pandas as pd
                    now = __import__('datetime').datetime.now()
                    days = 7 if interval <= 15 else 30
                    hist_df = historical.get_stock_historical_data(
                        symbol,
                        now - __import__('datetime').timedelta(days=days),
                        now,
                        interval=interval
                    )
                    live_df = live.get_stock_intraday_tick_by_tick_data(
                        symbol,
                        candle_interval=interval
                    )

                    frames = [x for x in (hist_df, live_df) if x is not None and len(x)]
                    if not frames:
                        df = None
                    elif len(frames) == 1:
                        df = frames[0]
                    else:
                        # Normalize column names just enough to concatenate sources.
                        # The later live frame is kept last so duplicate timestamps
                        # are replaced by the freshest NSE values below.
                        df = pd.concat(frames, axis=0, sort=False)

                if df is None or len(df) == 0:
                    self.send_json({'ok': False, 'error': 'No chart data available for this timeframe'}, 404); return

                cols = {str(x).lower().strip().replace(' ', '_'): x for x in df.columns}
                def find_col(*names):
                    for name in names:
                        key = name.lower().strip().replace(' ', '_')
                        if key in cols:
                            return cols[key]
                    return None

                ocol = find_col('open','open_price','openprice')
                hcol = find_col('high','high_price','highprice')
                lcol = find_col('low','low_price','lowprice')
                ccol = find_col('close','close_price','closeprice','ltp')
                vcol = find_col('volume','vol')
                if not all((ocol, hcol, lcol, ccol)):
                    self.send_json({'ok': False, 'error': 'NSE chart format changed; OHLC fields unavailable'}, 503); return

                out = []
                for idx, row in df.iterrows():
                    try:
                        o = float(row[ocol]); h = float(row[hcol])
                        lo = float(row[lcol]); cl = float(row[ccol])
                        vol = float(row[vcol]) if vcol is not None and row[vcol] is not None else 0.0
                        ts = idx
                        if not hasattr(ts, 'timestamp'):
                            ts = __import__('pandas').to_datetime(ts)
                        if hasattr(ts, 'to_pydatetime'):
                            ts = ts.to_pydatetime()
                        t = int(ts.timestamp())
                        if h < max(o, cl) or lo > min(o, cl):
                            continue
                        out.append({'time': t, 'open': o, 'high': h, 'low': lo, 'close': cl, 'volume': vol})
                    except Exception:
                        continue

                # Lightweight Charts requires strictly ascending, unique timestamps.
                # Keep the latest available trading session only, so 5m/15m charts
                # show a complete session instead of mixing several days.
                dedup = {}
                for x in out:
                    dedup[x['time']] = x
                ordered = [dedup[k] for k in sorted(dedup)]

                if ordered:
                    from datetime import datetime
                    latest_day = datetime.fromtimestamp(ordered[-1]['time']).date()
                    ordered = [
                        x for x in ordered
                        if datetime.fromtimestamp(x['time']).date() == latest_day
                    ]

                out = ordered[-1500:]
                if not out:
                    self.send_json({'ok': False, 'error': 'NSE returned chart rows but no valid OHLC candles'}, 503); return
                self.send_json({'ok': True, 'symbol': symbol, 'interval': str(interval), 'rows': out})
            except Exception as e:
                self.send_json({'ok': False, 'error': str(e)[:180]}, 503)
            return
        if path == '/api/options':
            qs=urllib.parse.parse_qs(query)
            symbol=(qs.get('symbol',[''])[0] or '').upper().strip()
            self.send_json(_nse_option_chain(symbol), 200); return

        if path == '/api/live_quote':
            qs=urllib.parse.parse_qs(query)
            symbol=(qs.get('symbol',[''])[0] or '').upper().strip()
            if not symbol:
                self.send_json({'ok':False,'error':'Missing symbol'},400); return
            try:
                q=live.get_stock_live_quotes(symbol)
                if not q:
                    self.send_json({'ok':False,'error':'Live quote unavailable'},503); return
                self.send_json({'ok':True,'symbol':symbol,'price':q.get('close'),'change':q.get('changepct'),'datetime':q.get('datetime')})
            except Exception as e:
                self.send_json({'ok':False,'error':str(e)[:160]},503)
            return
        if path == '/api/stock':
            qs=urllib.parse.parse_qs(query); symbol=(qs.get('symbol',[''])[0] or '').upper().strip()
            if not symbol:self.send_json({'ok':False,'error':'Missing symbol'},400);return
            row=next((x for x in app_auto.STATE.get('rows',[]) if x.get('symbol','').upper()==symbol),None)
            if row is None:self.send_json({'ok':False,'error':'Stock not found in current NSE snapshot'},404);return
            data=dict(row);refined=False
            try:
                tech=start.technical(symbol)
                if tech:data.update(tech);refined=True
            except Exception:pass
            self.send_json({'ok':True,'data':data,'refined':refined});return

        if path == '/api/nifty50':
            try:
                df = live.get_index_constituents_live_snapshot('NIFTY 50')
                if df is None or len(df) == 0:
                    self.send_json({'ok': False, 'error': 'NIFTY 50 constituent data unavailable'}, 503); return
                rows = []
                for _, r in df.iterrows():
                    sym = str(r.get('symbol', '')).strip()
                    if sym:
                        rows.append({'symbol': sym, 'price': r.get('ltp'), 'change': r.get('changepct'), 'weightage': r.get('weightage'), 'volume': r.get('volume'), 'turnover': r.get('turnover')})
                self.send_json({'ok': True, 'rows': rows, 'count': len(rows)}); return
            except Exception as e:
                self.send_json({'ok': False, 'error': str(e)[:180]}, 503); return
        if path == '/api/nifty50/index':
            try:
                q = live.get_index_live_price('NIFTY 50')
                if not q:
                    self.send_json({'ok': False, 'error': 'NIFTY 50 index unavailable'}, 503); return
                self.send_json({'ok': True, 'data': {'price': q.get('close'), 'change': q.get('changepct'), 'open': q.get('open'), 'high': q.get('high'), 'low': q.get('low'), 'previous_close': q.get('previous_close')}}); return
            except Exception as e:
                self.send_json({'ok': False, 'error': str(e)[:180]}, 503); return
        return super().do_GET()

app_auto.Handler=FastHandler

class NiftyHandler(FastHandler):
    def do_GET(self):
        path, _, query = self.path.partition('?')
        if path == '/api/nifty50':
            # Fast path: use the main scanner snapshot already held in memory.
            # Normalize NSE symbol variants such as NSE:RELIANCE, RELIANCE-EQ
            # and RELIANCE.NS before matching.
            rows_by_symbol = {}
            try:
                current = app_auto.STATE.get('rows', []) or []
                for r in current:
                    sym = _clean_nifty_symbol(r.get('symbol'))
                    if sym in NIFTY_SYMBOLS:
                        rows_by_symbol[sym] = {
                            'symbol': sym,
                            'price': r.get('price'),
                            'change': r.get('change'),
                            'weightage': None,
                            'volume': r.get('volume'),
                            'turnover': r.get('turnover')
                        }
            except Exception:
                pass

            # If the scanner snapshot uses an unexpected symbol format or has
            # not published yet, use the normal NSE equity snapshot once as a
            # fallback. This fixes an empty NIFTY panel without depending on
            # the slower dedicated index-constituent endpoint.
            if not rows_by_symbol:
                try:
                    df = app_auto.get_snapshot()
                    if df is not None and len(df):
                        for _, r in df.iterrows():
                            sym = _clean_nifty_symbol(r.get('symbol'))
                            if sym in NIFTY_SYMBOLS:
                                rows_by_symbol[sym] = {
                                    'symbol': sym,
                                    'price': r.get('close', r.get('ltp')),
                                    'change': r.get('changepct', r.get('change')),
                                    'weightage': r.get('weightage'),
                                    'volume': r.get('volume'),
                                    'turnover': r.get('turnover', r.get('traded_value'))
                                }
                except Exception:
                    pass

            rows = [rows_by_symbol.get(s, {
                'symbol': s, 'price': None, 'change': None,
                'weightage': None, 'volume': None, 'turnover': None
            }) for s in NIFTY_SYMBOLS]

            valid = [r for r in rows if r.get('price') is not None]
            breadth = {
                'advance': sum(1 for r in valid if float(r.get('change') or 0) > 0),
                'decline': sum(1 for r in valid if float(r.get('change') or 0) < 0),
                'unchanged': sum(1 for r in valid if float(r.get('change') or 0) == 0)
            }
            self.send_json({
                'ok': True,
                'rows': rows,
                'count': len(rows),
                'breadth': breadth,
                'error': '' if valid else 'Waiting for NSE snapshot',
                'ts': app_auto.STATE.get('ts', 0)
            }, 200)
            return
        if path == '/api/nifty50/index':
            with NIFTY_LOCK:
                q=dict(NIFTY_CACHE['index'])
            self.send_json({'ok':bool(q),'data':q,'breadth':dict(NIFTY_CACHE.get('breadth',{})),'error':None if q else 'NIFTY index quote loading'}, 200); return
        return super().do_GET()

if __name__=='__main__':
    threading.Thread(target=app_auto.auto_loop,daemon=True).start()
    threading.Thread(target=refresh_nifty_cache,daemon=True).start()
    start_background_scan(force=True)
    port=int(os.environ.get('PORT','10000'))
    app_auto.ThreadingHTTPServer(('0.0.0.0',port),NiftyHandler).serve_forever()