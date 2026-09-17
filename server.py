import os
import threading
import time
import urllib.parse
import app_auto
import start

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
<div id="dsub" class="small"></div><div id="details" class="detailgrid"></div>
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
async function openStock(sym){sym=decodeURIComponent(sym);$('modal').style.display='flex';$('dtitle').textContent=sym;$('dsub').textContent='Loading latest stock data…';$('details').innerHTML='<div class="empty">Fetching…</div>';try{let r=await fetch('/api/stock?symbol='+encodeURIComponent(sym));let j=await r.json();if(!j.ok)throw Error(j.error||'Failed');let x=j.data;$('dsub').textContent='NSE • '+(j.refined?'5-minute AI refined':'snapshot data');let items=[['Price',money(x.price)],['Change',val(x.change)+'%'],['AI Score',val(x.score)],['Signal',val(x.signal)],['Confidence',val(x.ai_confidence)+'%'],['Model',val(x.ai_model)],['RSI',val(x.rsi)],['EMA 9',money(x.ema9)],['EMA 21',money(x.ema21)],['Momentum',val(x.momentum)+'%'],['Volume',Number(x.volume||0).toLocaleString('en-IN')],['Target',money(x.target)],['Stop Loss',money(x.sl)]];$('details').innerHTML=items.map(a=>'<div class="detail"><b>'+a[0]+'</b><span>'+a[1]+'</span></div>').join('');$('dreason').textContent=x.ai_reason||'No additional AI explanation available.'}catch(e){$('details').innerHTML='<div class="empty">'+e.message+'</div>';$('dsub').textContent='Unable to load stock details'}}
function closeModal(){$('modal').style.display='none'}
$('budget').addEventListener('input',()=>render());$('search').addEventListener('input',()=>{lastQuery=$('search').value;render()});
state();setInterval(state,1500);
</script></body></html>'''

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
        return super().do_GET()

app_auto.Handler=FastHandler

if __name__=='__main__':
    threading.Thread(target=app_auto.auto_loop,daemon=True).start()
    start_background_scan(force=True)
    port=int(os.environ.get('PORT','10000'))
    app_auto.ThreadingHTTPServer(('0.0.0.0',port),FastHandler).serve_forever()
