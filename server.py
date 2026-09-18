import os
import threading
import time
import urllib.parse
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
<title>Intraday AI</title>
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
.spinner{display:inline-block;width:13px;height:13px;border:2px solid #cbd5e1;border-top-color:#111827;border-radius:50%;animation:spin .7s linear infinite;vertical-align:-2px}@keyframes spin{to{transform:rotate(360deg)}}
@media(max-width:900px){.recommend{grid-template-columns:1fr}.detailgrid{grid-template-columns:repeat(2,1fr)}.wrap{padding:10px}}
@media(max-width:600px){header{padding:15px 12px}h1{font-size:23px}.panel{padding:13px}.controls input,.controls button,.search{width:100%;min-height:44px}.searchrow{display:grid;grid-template-columns:1fr}.recommend{grid-template-columns:1fr}.detailgrid{grid-template-columns:1fr 1fr}table{min-width:1000px}th,td{font-size:12px;padding:9px 7px}}

.chart-toolbar{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:15px}
.chart-toolbar button{padding:7px 10px;font-size:12px}
#priceChart{width:100%;height:360px;margin-top:8px;border:1px solid #e2e7ed;border-radius:10px;overflow:hidden}
.chart-note{font-size:11px;color:#687386;margin:6px 0 12px}
@media(max-width:600px){#priceChart{height:300px}.chart-toolbar button{min-width:45px}}
</style></head>
<body>
<header><h1>Intraday AI</h1><div class="sub">NSE intraday AI scanner • multi-stock search • live recommendation</div>
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
<div class="chart-note">Candlestick + volume • NSE historical data</div>
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
async function openStock(sym){sym=decodeURIComponent(sym);activeChartSymbol=sym;$('modal').style.display='flex';$('dtitle').textContent=sym;$('dsub').textContent='Loading latest stock data…';$('details').innerHTML='<div class="empty">Fetching…</div>';loadChart('5');try{let r=await fetch('/api/stock?symbol='+encodeURIComponent(sym));let j=await r.json();if(!j.ok)throw Error(j.error||'Failed');let x=j.data;$('dsub').textContent='NSE • '+(j.refined?'5-minute AI refined':'snapshot data');let items=[['Price',money(x.price)],['Change',val(x.change)+'%'],['AI Score',val(x.score)],['Signal',val(x.signal)],['Confidence',val(x.ai_confidence)+'%'],['Model',val(x.ai_model)],['RSI',val(x.rsi)],['EMA 9',money(x.ema9)],['EMA 21',money(x.ema21)],['VWAP',money(x.vwap)],['MACD',val(x.macd)],['MACD Signal',val(x.macd_signal)],['ADX',val(x.adx)],['Relative Volume',val(x.relative_volume)+'x'],['ATR',money(x.atr)],['Momentum',val(x.momentum)+'%'],['Volume',Number(x.volume||0).toLocaleString('en-IN')],['Target',money(x.target)],['Stop Loss',money(x.sl)]];$('details').innerHTML=items.map(a=>'<div class="detail"><b>'+a[0]+'</b><span>'+a[1]+'</span></div>').join('');$('dreason').textContent=x.ai_reason||'No additional AI explanation available.'}catch(e){$('details').innerHTML='<div class="empty">'+e.message+'</div>';$('dsub').textContent='Unable to load stock details'}}
function closeModal(){$('modal').style.display='none'}
$('budget').addEventListener('input',()=>render());$('search').addEventListener('input',()=>{lastQuery=$('search').value;render()});
state();setInterval(state,1500);

let NIFTY50_DIRECT=[];
function niftyMoney(v){return v==null?'—':'₹'+Number(v).toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2});}
function renderNifty50(){
 const q=($('niftySearch').value||'').trim().toUpperCase();
 const a=NIFTY50_DIRECT.filter(x=>!q||x.symbol.toUpperCase().includes(q));
 $('niftyInfo').textContent='Showing '+a.length+' of '+NIFTY50_DIRECT.length+' NIFTY 50 stocks';
 $('niftyRows').innerHTML=a.map(x=>{
  const ch=x.change==null?null:Number(x.change);
  return '<tr class="stockrow"><td><b>'+x.symbol+'</b></td><td>'+niftyMoney(x.price)+'</td><td class="'+(ch!=null&&ch>=0?'green':'red')+'">'+(ch==null?'—':(ch>=0?'+':'')+ch.toFixed(2)+'%')+'</td><td>'+(x.weightage==null?'—':Number(x.weightage).toFixed(2)+'%')+'</td><td>'+(x.volume==null?'—':Number(x.volume).toLocaleString('en-IN'))+'</td><td>'+(x.turnover==null?'—':Number(x.turnover).toLocaleString('en-IN'))+'</td></tr>';
 }).join('')||'<tr><td colspan="6" class="empty">No matching NIFTY 50 stock.</td></tr>';
}
async function loadNifty50Direct(){
 try{
  const [ir,cr]=await Promise.all([fetch('/api/nifty50/index'),fetch('/api/nifty50')]);
  const ij=await ir.json(),cj=await cr.json();
  if(ij.ok){const d=ij.data,ch=Number(d.change||0);$('niftyIndex').innerHTML='<b style="font-size:22px">NIFTY 50 '+niftyMoney(d.price)+'</b> <span class="'+(ch>=0?'green':'red')+'" style="font-size:18px;font-weight:800;margin-left:12px">'+(ch>=0?'+':'')+ch.toFixed(2)+'%</span><div class="small" style="margin-top:6px">Open '+niftyMoney(d.open)+' · High '+niftyMoney(d.high)+' · Low '+niftyMoney(d.low)+' · Prev '+niftyMoney(d.previous_close)+'</div>';}
  if(cj.ok){NIFTY50_DIRECT=cj.rows||[];renderNifty50();} else $('niftyRows').innerHTML='<tr><td colspan="6" class="empty">'+(cj.error||'NIFTY 50 unavailable')+'</td></tr>';
 }catch(e){$('niftyIndex').textContent='NIFTY 50 data unavailable';}
}
$('niftySearch').addEventListener('input',renderNifty50);
loadNifty50Direct();setInterval(loadNifty50Direct,15000);

let activeChartSymbol='', activeChart=null, activeCandleSeries=null, activeVolumeSeries=null;
async function loadChart(interval='5'){
  if(!activeChartSymbol)return;
  $('chartStatus').textContent='Loading '+interval+' chart…';
  try{
    const r=await fetch('/api/chart?symbol='+encodeURIComponent(activeChartSymbol)+'&interval='+encodeURIComponent(interval));
    const j=await r.json();
    if(!j.ok)throw Error(j.error||'Chart unavailable');
    const el=$('priceChart');
    if(activeChart){try{activeChart.remove()}catch(e){}}
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
    activeCandleSeries.setData(j.rows.map(x=>({time:x.time,open:x.open,high:x.high,low:x.low,close:x.close})));
    activeVolumeSeries=activeChart.addHistogramSeries({
      priceFormat:{type:'volume'},priceScaleId:'volume',
      scaleMargins:{top:0.82,bottom:0}
    });
    activeVolumeSeries.setData(j.rows.map(x=>({time:x.time,value:x.volume,color:x.close>=x.open?'#86efac':'#fca5a5'})));
    activeChart.timeScale().fitContent();
    $('chartStatus').textContent=(j.rows.length)+' candles • '+interval+' timeframe';
    window.addEventListener('resize',()=>{if(activeChart)activeChart.resize(el.clientWidth,el.clientHeight)});
  }catch(e){$('chartStatus').textContent='Chart error: '+e.message}
}
</script></body></html>'''


# NIFTY 50 background cache: never block browser requests on NSE.
NIFTY_CACHE = {'rows': [], 'index': {}, 'error': '', 'ts': 0}
NIFTY_LOCK = threading.Lock()
NIFTY_SYMBOLS = [
    'ADANIENT','ADANIPORTS','APOLLOHOSP','ASIANPAINT','AXISBANK','BAJAJ-AUTO',
    'BAJAJFINSV','BAJFINANCE','BEL','BHARTIARTL','CIPLA','COALINDIA','DRREDDY',
    'EICHERMOT','ETERNAL','GRASIM','HCLTECH','HDFCBANK','HDFCLIFE','HINDALCO',
    'HINDUNILVR','ICICIBANK','INDIGO','INFY','ITC','JIOFIN','JSWSTEEL','KOTAKBANK',
    'LT','M&M','MARUTI','MAXHEALTH','NESTLEIND','NTPC','ONGC','POWERGRID','RELIANCE',
    'SBILIFE','SBIN','SHRIRAMFIN','SUNPHARMA','TATACONSUM','TATASTEEL','TCS','TECHM',
    'TITAN','TRENT','ULTRACEMCO','WIPRO'
]

def refresh_nifty_cache():
    while True:
        try:
            df = live.get_index_constituents_live_snapshot('NIFTY 50')
            rows_by_symbol = {}
            if df is not None and len(df):
                for _, r in df.iterrows():
                    sym=str(r.get('symbol','')).strip()
                    if sym:
                        rows_by_symbol[sym.upper()] = {
                            'symbol':sym,'price':r.get('ltp'),'change':r.get('changepct'),
                            'weightage':r.get('weightage'),'volume':r.get('volume'),
                            'turnover':r.get('turnover')
                        }
            # Fill missing constituents from the complete NSE EQ snapshot.
            if len(rows_by_symbol) < 50:
                eq = live.get_all_securities_live_snapshot(series='EQ')
                if eq is not None and len(eq):
                    for _, r in eq.iterrows():
                        sym=str(r.get('symbol','')).strip()
                        if sym.upper() in NIFTY_SYMBOLS and sym.upper() not in rows_by_symbol:
                            rows_by_symbol[sym.upper()] = {
                                'symbol':sym,'price':r.get('close'),'change':r.get('changepct'),
                                'weightage':None,'volume':r.get('volume'),
                                'turnover':r.get('traded_value')
                            }
            # Always render all 50 slots in the fixed NIFTY 50 order.
            # Missing live quotes are kept as placeholders instead of hiding the constituent.
            rows=[]
            for s in NIFTY_SYMBOLS:
                if s in rows_by_symbol:
                    rows.append(rows_by_symbol[s])
                else:
                    rows.append({'symbol':s,'price':None,'change':None,'weightage':None,'volume':None,'turnover':None})
            idx=live.get_index_live_price('NIFTY 50') or {}
            with NIFTY_LOCK:
                missing=sum(1 for r in rows if r.get('price') is None)
                NIFTY_CACHE.update({'rows':rows[:50],'index':idx,'error':('Live quote missing for '+str(missing)+' NIFTY 50 stock(s)') if missing else '','ts':time.time()})
        except Exception as e:
            with NIFTY_LOCK:
                NIFTY_CACHE['error']=str(e)[:180]
        time.sleep(15)

# NIFTY50 UI injection
_NIFTY50_PANEL = '''
<div class="panel nifty-panel"><h2>🇮🇳 NIFTY 50</h2><div id="niftyIndex" class="nifty-index">Loading NIFTY 50…</div><div class="searchrow"><input id="niftySearch" class="search" placeholder="🔍 Search NIFTY 50 stock"><span id="niftyInfo" class="searchinfo"></span></div><div class="tablewrap nifty-table"><table><thead><tr><th>Stock</th><th>Price</th><th>Change</th><th>Weight</th><th>Volume</th><th>Turnover</th></tr></thead><tbody id="niftyRows"><tr><td colspan="6" class="empty">Loading…</td></tr></tbody></table></div></div>
'''
_NIFTY50_SCRIPT = '''<style>.nifty-index{display:flex;gap:18px;align-items:center;flex-wrap:wrap;background:#f6f8fa;border-radius:12px;padding:14px;margin-bottom:14px}.nifty-main{font-size:24px;font-weight:900}.nifty-change{font-size:18px;font-weight:800}.nifty-meta{font-size:12px;color:#687386}.nifty-panel{margin:16px 18px 0}.nifty-table{overflow:visible}.nifty-table table{min-width:0}.nifty-table th,.nifty-table td{padding:9px 7px}@media(max-width:600px){.nifty-panel{margin:10px}.nifty-table table{display:block}.nifty-table thead{display:none}.nifty-table tbody{display:grid;grid-template-columns:1fr 1fr;gap:8px}.nifty-table tr{display:grid;grid-template-columns:1fr auto;gap:2px 8px;border:1px solid #e5e9ef;border-radius:10px;padding:9px;background:#fff}.nifty-table td{border:0;padding:2px 0;white-space:normal}.nifty-table td:nth-child(1){font-size:14px}.nifty-table td:nth-child(2){text-align:right}.nifty-table td:nth-child(3){text-align:right}.nifty-table td:nth-child(4),.nifty-table td:nth-child(5),.nifty-table td:nth-child(6){font-size:11px;color:#687386}.nifty-table td:nth-child(4)::before{content:'Wt ';}.nifty-table td:nth-child(5)::before{content:'Vol ';}.nifty-table td:nth-child(6)::before{content:'Turn ';}}@media(max-width:420px){.nifty-table tbody{grid-template-columns:1fr}}</style><script>
let NIFTY50=[];const n$=id=>document.getElementById(id);function niftyMoney(x){return x==null?'—':'₹'+Number(x).toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2});}
function niftyRender(){const q=(n$('niftySearch').value||'').trim().toUpperCase();const rows=NIFTY50.filter(x=>!q||x.symbol.toUpperCase().includes(q));n$('niftyInfo').textContent='Showing '+rows.length+' of '+NIFTY50.length+' NIFTY 50 stocks';n$('niftyRows').innerHTML=rows.map(x=>{const ch=x.change==null?null:Number(x.change);const cls=ch!=null?(ch>=0?'green':'red'):'';return '<tr><td><b>'+x.symbol+'</b></td><td>'+niftyMoney(x.price)+'</td><td class="'+cls+'">'+(ch==null?'—':ch.toFixed(2)+'%')+'</td><td>'+(x.weightage==null?'—':Number(x.weightage).toFixed(2)+'%')+'</td><td>'+(x.volume==null?'—':Number(x.volume).toLocaleString('en-IN'))+'</td><td>'+(x.turnover==null?'—':Number(x.turnover).toLocaleString('en-IN'))+'</td></tr>';}).join('')||'<tr><td colspan="6" class="empty">No matching NIFTY 50 stock.</td></tr>';}
async function loadNifty50(){try{const [ir,cr]=await Promise.all([fetch('/api/nifty50/index'),fetch('/api/nifty50')]);const ij=await ir.json(),cj=await cr.json();if(ij.ok){const d=ij.data,ch=Number(d.changepct||d.change||0);n$('niftyIndex').innerHTML='<span class="nifty-main">NIFTY 50 '+niftyMoney(d.close)+'</span><span class="nifty-change '+(ch>=0?'green':'red')+'">'+(ch>=0?'+':'')+ch.toFixed(2)+'%</span><span class="nifty-meta">O '+niftyMoney(d.open)+' · H '+niftyMoney(d.high)+' · L '+niftyMoney(d.low)+' · Prev '+niftyMoney(d.previous_close)+'</span>';}if(cj.ok){NIFTY50=cj.rows||[];niftyRender();}else n$('niftyRows').innerHTML='<tr><td colspan="6" class="empty">'+(cj.error||'NIFTY 50 data unavailable')+'</td></tr>';}catch(e){n$('niftyIndex').textContent='NIFTY 50 connection error';}}
n$('niftySearch').addEventListener('input',niftyRender);loadNifty50();setInterval(loadNifty50,15000);</script>'''
app_auto.HTML = app_auto.HTML.replace('<div class="wrap">', _NIFTY50_PANEL + '<div class="wrap">' + _NIFTY50_SCRIPT)

class FastHandler(app_auto.Handler):
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
                    # Prefer NSE's current-day candle endpoint for intraday charts.
                    df = live.get_stock_intraday_tick_by_tick_data(symbol, candle_interval=interval)
                    # Before/after market hours, fall back to historical intraday data.
                    if df is None or len(df) == 0:
                        days = 7 if interval <= 15 else 30
                        df = historical.get_stock_historical_data(
                            symbol,
                            __import__('datetime').datetime.now() - __import__('datetime').timedelta(days=days),
                            __import__('datetime').datetime.now(),
                            interval=interval
                        )

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
                dedup = {}
                for x in out:
                    dedup[x['time']] = x
                out = [dedup[k] for k in sorted(dedup)]
                out = out[-1500:]
                if not out:
                    self.send_json({'ok': False, 'error': 'NSE returned chart rows but no valid OHLC candles'}, 503); return
                self.send_json({'ok': True, 'symbol': symbol, 'interval': str(interval), 'rows': out})
            except Exception as e:
                self.send_json({'ok': False, 'error': str(e)[:180]}, 503)
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
            with NIFTY_LOCK:
                payload={'ok':bool(NIFTY_CACHE['rows']),'rows':list(NIFTY_CACHE['rows']),'count':len(NIFTY_CACHE['rows']),'error':NIFTY_CACHE['error'],'ts':NIFTY_CACHE['ts']}
            self.send_json(payload, 200 if payload['ok'] else 503); return
        if path == '/api/nifty50/index':
            with NIFTY_LOCK:
                q=dict(NIFTY_CACHE['index'])
            self.send_json({'ok':bool(q),'data':q,'error':None if q else 'NIFTY 50 index data loading'}, 200 if q else 503); return
        return super().do_GET()

if __name__=='__main__':
    threading.Thread(target=app_auto.auto_loop,daemon=True).start()
    threading.Thread(target=refresh_nifty_cache,daemon=True).start()
    start_background_scan(force=True)
    port=int(os.environ.get('PORT','10000'))
    app_auto.ThreadingHTTPServer(('0.0.0.0',port),NiftyHandler).serve_forever()
