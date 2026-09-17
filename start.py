import os
import threading
import time
import sitecustomize  # noqa: F401
import app_auto
from ai_engine import analyze as ai_analyze
from nsemine import live


def technical(symbol):
    cached = app_auto.STATE['technical_cache'].get(symbol)
    if cached and time.time() - cached['ts'] < app_auto.TECH_TTL:
        return cached['data']

    try:
        from nsemine import historical
        from datetime import datetime, timedelta
        end = datetime.now()
        start = end - timedelta(days=7)
        df = historical.get_stock_historical_data(symbol, start, end, interval=5)
        if df is not None and len(df) >= 30:
            data = ai_analyze(symbol, df)
            if data:
                app_auto.STATE['technical_cache'][symbol] = {'ts': time.time(), 'data': data}
                return data
    except Exception:
        pass

    try:
        df = live.get_stock_intraday_tick_by_tick_data(symbol, candle_interval=5)
        if df is not None and len(df) >= 30:
            data = ai_analyze(symbol, df)
            if data:
                app_auto.STATE['technical_cache'][symbol] = {'ts': time.time(), 'data': data}
                return data
    except Exception:
        pass

    # Explicit fallback from the live NSE snapshot when 5-minute candles are unavailable.
    snap = getattr(app_auto, 'AI_SNAPSHOT', {}).get(symbol)
    if not snap:
        return None
    price = float(snap.get('price', 0) or 0)
    change = float(snap.get('change', 0) or 0)
    volume = float(snap.get('volume', 0) or 0)
    all_volumes = [float(v.get('volume', 0) or 0) for v in getattr(app_auto, 'AI_SNAPSHOT', {}).values()]
    max_volume = max(max(all_volumes, default=0), 1)
    volume_boost = min(8.0, (volume / max_volume) * 8.0)
    score = max(0.0, min(100.0, 50.0 + change * 1.6 + volume_boost))
    confidence = max(50.0, min(78.0, 50.0 + abs(change) * 1.3 + volume_boost * 0.35))
    if change >= 5.0 and score >= 68.0:
        signal = 'BUY'
    elif change <= -5.0 and score <= 32.0:
        signal = 'SELL'
    else:
        signal = 'WAIT'
    target = price * (1.012 if signal == 'BUY' else 0.988) if signal in ('BUY', 'SELL') else None
    sl = price * (0.99 if signal == 'BUY' else 1.01) if signal in ('BUY', 'SELL') else None
    data = {
        'price': round(price, 2), 'momentum': round(change, 3),
        'score': round(score, 1), 'signal': signal,
        'target': round(target, 2) if target else None,
        'sl': round(sl, 2) if sl else None,
        'ai_probability': round(score / 100.0, 4),
        'ai_confidence': round(confidence, 1),
        'ai_direction': 'UP' if change >= 0 else 'DOWN',
        'ai_model': 'AI-FALLBACK',
        'ai_validation': None,
        'ai_reason': 'live NSE snapshot fallback; 5-minute history unavailable',
    }
    app_auto.STATE['technical_cache'][symbol] = {'ts': time.time(), 'data': data}
    return data


def scan():
    if app_auto.STATE['rows'] and time.time() - app_auto.STATE['ts'] < app_auto.SCAN_TTL:
        return app_auto.STATE['rows']
    with app_auto.STATE['lock']:
        if app_auto.STATE['rows'] and time.time() - app_auto.STATE['ts'] < app_auto.SCAN_TTL:
            return app_auto.STATE['rows']
        app_auto.STATE['scanning'] = True
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
                        'ai_reason': None
                    })
                except Exception:
                    continue
            if not rows:
                raise RuntimeError('NSE live market data had no usable equity rows')

            app_auto.AI_SNAPSHOT = {x['symbol']: x for x in rows}
            candidates = sorted(rows, key=lambda x: (abs(x['change']), x['volume']), reverse=True)[:6]
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=3) as ex:
                futures = {ex.submit(technical, x['symbol']): x for x in candidates}
                for f in as_completed(futures):
                    try:
                        data = f.result()
                        if data:
                            futures[f].update(data)
                    except Exception:
                        pass

            candidate_symbols = {x['symbol'] for x in candidates}
            for x in rows:
                if x['symbol'] not in candidate_symbols:
                    x['signal'] = 'WAIT'
            rows.sort(key=lambda x: x['score'], reverse=True)
            app_auto.STATE['rows'], app_auto.STATE['ts'], app_auto.STATE['last_error'] = rows, time.time(), ''
            return rows
        except Exception as e:
            app_auto.STATE['last_error'] = str(e)[:160]
            if app_auto.STATE['rows']:
                return app_auto.STATE['rows']
            raise
        finally:
            app_auto.STATE['scanning'] = False


app_auto.technical = technical
app_auto.scan = scan

HTML = '''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Intraday AI</title><style>*{box-sizing:border-box}body{margin:0;background:#f4f6f8;color:#172033;font-family:Arial,sans-serif}header{background:#101827;color:#fff;padding:22px 28px}h1{margin:0;font-size:28px}.sub{opacity:.7;margin-top:5px}.wrap{max-width:1450px;margin:auto;padding:18px}.panel{background:#fff;border-radius:14px;padding:18px;margin-bottom:16px;box-shadow:0 2px 12px #0001}.controls{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.controls label{font-weight:800}input,button{padding:11px 13px;border:1px solid #ccd3dd;border-radius:8px;font-size:15px}input{width:170px}button{background:#111827;color:#fff;font-weight:800;cursor:pointer}.status{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px}.chip{background:#1d2939;padding:8px 11px;border-radius:8px}.recommend{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.rec{border:1px solid #e2e7ed;border-radius:12px;padding:15px}.rec.buy{border-left:5px solid #0a8f45}.rec.sell{border-left:5px solid #d62828}.symbol{font-size:20px;font-weight:800}.price{font-size:18px;margin-top:5px}.meta{font-size:13px;color:#687386;margin-top:7px}.badge{display:inline-block;padding:5px 9px;border-radius:20px;background:#edf2f7;font-weight:800;font-size:12px}.green{color:#07833a}.red{color:#c62828}table{width:100%;border-collapse:collapse}th,td{padding:10px 8px;border-bottom:1px solid #edf0f4;text-align:left;white-space:nowrap}th{font-size:11px;color:#697386}.small{font-size:12px;color:#697386}.empty{padding:20px;text-align:center;color:#697386}.hint{font-size:12px;color:#687386;margin-top:8px}@media(max-width:900px){.recommend{grid-template-columns:1fr}.wrap{padding:10px}.controls{align-items:stretch}.controls input,.controls button{width:100%}}</style></head><body><header><h1>Intraday AI</h1><div class="sub">Simple NSE intraday AI scanner</div><div class="status"><span class="chip" id="market">MARKET --</span><span class="chip" id="last">Last scan: --</span><span class="chip">AUTO SCAN ON</span></div></header><div class="wrap"><div class="panel"><div class="controls"><label for="budget">Investment Budget ₹</label><input id="budget" type="number" value="5000" min="0" step="100"><button onclick="scanNow()">SCAN NSE</button><span id="msg" class="small">Loading…</span></div><div class="hint">Qty is calculated directly from your Investment Budget. Example: ₹50,000 budget means the maximum quantity is ₹50,000 ÷ stock price.</div></div><div class="panel"><h2>🤖 AI RECOMMENDATION</h2><div id="recommendations" class="recommend"><div class="empty">Scanning NSE…</div></div></div><div class="panel"><h2>📊 AI STOCK LIST</h2><div class="small" style="margin-bottom:10px">AI-scanned candidates are shown first. Quantity is based only on your Investment Budget.</div><div style="overflow:auto"><table><thead><tr><th>Stock</th><th>Price</th><th>AI Score</th><th>Confidence</th><th>Model</th><th>Signal</th><th>Qty</th><th>Investment</th><th>Target</th><th>Stop Loss</th></tr></thead><tbody id="rows"></tbody></table></div></div><div class="panel small">ML = historical 5-minute model. AI-FALLBACK = clearly marked NSE snapshot fallback. Signals are probabilistic, not guaranteed.</div></div><script>const $=id=>document.getElementById(id);const money=x=>x==null?'—':'₹'+Number(x).toFixed(2);const fmt=x=>x==null?'—':x;function market(){let d=new Date(),m=d.getHours()*60+d.getMinutes();$('market').textContent='MARKET '+(d.getDay()==0||d.getDay()==6?'CLOSED':m<555?'PRE-OPEN':m<=930?'OPEN':'CLOSED')}setInterval(market,1000);market();function qty(x){let b=Math.max(0,Number($('budget').value||0));return x.price>0?Math.floor(b/x.price):0}function card(x){let q=qty(x);return '<div class="rec '+(x.signal==='BUY'?'buy':'sell')+'"><div class="symbol">'+x.symbol+'</div><div class="price">'+money(x.price)+' <span class="badge '+(x.signal==='BUY'?'green':'red')+'">'+x.signal+'</span></div><div class="meta">AI Score <b>'+fmt(x.score)+'</b> · Confidence <b>'+fmt(x.ai_confidence)+'%</b></div><div class="meta">Model: <b>'+fmt(x.ai_model)+'</b></div><div class="meta">Qty <b>'+q+'</b> · Investment <b>'+money(q*x.price)+'</b></div><div class="meta">Target <b>'+money(x.target)+'</b> · SL <b>'+money(x.sl)+'</b></div></div>'}function render(j){let rows=j.rows||[];$('last').textContent='Last scan: '+(j.ts?new Date(j.ts*1000).toLocaleTimeString('en-IN'):'--');$('msg').textContent=j.error?'Using cached NSE data':j.scanning?'Scanning NSE…':'Live NSE data';let rec=rows.filter(x=>x.signal==='BUY'||x.signal==='SELL').sort((a,b)=>Number(b.score)-Number(a.score)).slice(0,3);$('recommendations').innerHTML=rec.length?rec.map(card).join(''):'<div class="empty">No AI BUY/SELL recommendation right now.</div>';$('rows').innerHTML=rows.slice(0,100).map(x=>{let q=qty(x);return '<tr><td><b>'+x.symbol+'</b></td><td>'+money(x.price)+'</td><td><b>'+fmt(x.score)+'</b></td><td>'+fmt(x.ai_confidence)+'%</td><td>'+fmt(x.ai_model)+'</td><td><span class="badge">'+x.signal+'</span></td><td>'+q+'</td><td>'+money(q*x.price)+'</td><td>'+money(x.target)+'</td><td>'+money(x.sl)+'</td></tr>'}).join('')}async function state(){try{let r=await fetch('/api/state');render(await r.json())}catch(e){$('msg').textContent='Connection error'}}async function scanNow(){$('msg').textContent='Refreshing NSE…';try{await fetch('/api/scan')}catch(e){}await state()}$('budget').addEventListener('input',state);state();setInterval(state,30000);</script></body></html>'''
app_auto.HTML = HTML

if __name__ == '__main__':
    threading.Thread(target=app_auto.auto_loop, daemon=True).start()
    port = int(os.environ.get('PORT', '10000'))
    app_auto.ThreadingHTTPServer(('0.0.0.0', port), app_auto.Handler).serve_forever()
