import json, os, threading, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

from nsemine import live, nse

DATA_DIR = os.environ.get('DATA_DIR', '/tmp/intraday_ai_data')
os.makedirs(DATA_DIR, exist_ok=True)
SETTINGS_FILE = os.path.join(DATA_DIR, 'auto_settings.json')

STATE = {
    'rows': [], 'ts': 0, 'universe': [], 'universe_ts': 0,
    'positions': [], 'signals': [], 'last_summary': '', 'last_auto': '',
    'lock': threading.Lock()
}


def now_ist():
    return datetime.now(ZoneInfo('Asia/Kolkata'))


def market_state():
    d = now_ist()
    if d.weekday() >= 5:
        return 'CLOSED'
    m = d.hour * 60 + d.minute
    if m < 555:
        return 'PRE-OPEN'
    if m <= 930:
        return 'OPEN'
    return 'CLOSED'


def load_settings():
    try:
        with open(SETTINGS_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'bot_token': '', 'chat_id': '', 'alerts': True, 'auto': True,
                'budget': 5000, 'risk': 500, 'max_trades': 2}


def save_settings(x):
    old = load_settings()
    old.update(x)
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(old, f)
    return old


def telegram(text):
    s = load_settings()
    if not s.get('alerts') or not s.get('bot_token') or not s.get('chat_id'):
        return False
    try:
        u = 'https://api.telegram.org/bot' + str(s['bot_token']) + '/sendMessage'
        d = urllib.parse.urlencode({'chat_id': s['chat_id'], 'text': text}).encode()
        req = urllib.request.Request(u, data=d, headers={'User-Agent': 'IntradayAI'})
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status == 200
    except Exception:
        return False


def ema(a, n):
    if len(a) < n:
        return None
    k = 2 / (n + 1)
    e = sum(a[:n]) / n
    for x in a[n:]:
        e = x * k + e * (1 - k)
    return e


def rsi(a, n=14):
    if len(a) < n + 1:
        return None
    gains, losses = [], []
    for x, y in zip(a[-n-1:-1], a[-n:]):
        d = y - x
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag, al = sum(gains) / n, sum(losses) / n
    return 100 if al == 0 else 100 - 100 / (1 + ag / al)


def technical(symbol):
    try:
        df = live.get_stock_intraday_tick_by_tick_data(symbol, candle_interval=5)
        if df is None or len(df) < 25:
            return None
        cols = {str(c).lower(): c for c in df.columns}
        cc = cols.get('close') or cols.get('last')
        if not cc:
            return None
        closes = [float(x) for x in df[cc].tolist() if x is not None]
        if len(closes) < 25:
            return None
        p = closes[-1]
        e9, e21, rr = ema(closes, 9), ema(closes, 21), rsi(closes)
        mom = (p / closes[-2] - 1) * 100
        bull = bear = 0
        if p > e9: bull += 25
        else: bear += 25
        if p > e21: bull += 25
        else: bear += 25
        if e9 > e21: bull += 20
        else: bear += 20
        if rr is not None:
            if 52 <= rr <= 68: bull += 15
            elif 32 <= rr <= 48: bear += 15
        if mom > 0: bull += 15
        elif mom < 0: bear += 15
        score = max(0, min(100, 50 + bull - bear))
        signal = 'BUY' if bull >= 70 and bull - bear >= 30 else 'SELL' if bear >= 70 and bear - bull >= 30 else 'WAIT'
        return {'price': round(p, 2), 'rsi': round(rr, 1) if rr is not None else None,
                'ema9': round(e9, 2), 'ema21': round(e21, 2), 'momentum': round(mom, 3),
                'score': round(score, 1), 'signal': signal, 'detail': True}
    except Exception:
        return None


def get_universe():
    if STATE['universe'] and time.time() - STATE['universe_ts'] < 86400:
        return STATE['universe']
    try:
        df = nse.get_all_equities_list()
        if df is not None and len(df):
            df = df[df['series'].astype(str).str.upper().eq('EQ')]
            symbols = sorted(set(df['symbol'].astype(str).str.strip()))
            if symbols:
                STATE['universe'] = symbols
                STATE['universe_ts'] = time.time()
                return symbols
    except Exception:
        pass
    return STATE['universe']


def scan(force=False):
    if not force and STATE['rows'] and time.time() - STATE['ts'] < 45:
        return STATE['rows']
    live_df = live.get_all_securities_live_snapshot(series='EQ')
    if live_df is None or len(live_df) == 0:
        if STATE['rows']:
            return STATE['rows']
        raise RuntimeError('NSE live market data unavailable')
    universe = get_universe()
    if not universe:
        universe = sorted(set(live_df['symbol'].astype(str).str.strip()))
    live_df = live_df.copy()
    live_df['symbol'] = live_df['symbol'].astype(str).str.strip()
    live_df = live_df.drop_duplicates('symbol')
    mp = {r['symbol']: r for _, r in live_df.iterrows()}
    rows = []
    for sym in universe:
        r = mp.get(sym)
        if r is None:
            continue
        try:
            price = float(r['close'])
            change = float(r['changepct'])
            volume = int(r['volume']) if r.get('volume') is not None else 0
            rows.append({'symbol': sym, 'price': round(price, 2), 'change': round(change, 2),
                         'volume': volume, 'score': 50, 'signal': 'WAIT', 'rsi': None,
                         'ema9': None, 'ema21': None, 'momentum': None, 'source': 'NSE'})
        except Exception:
            continue
    usable = rows
    max_vol = max([x.get('volume', 0) for x in usable] or [1])
    for x in usable:
        ch = x['change']
        score = 50 + max(-25, min(25, ch * 8)) + min(10, x['volume'] / max_vol * 10)
        x['score'] = round(max(0, min(100, score)), 1)
        x['signal'] = 'BUY' if ch >= 2 else 'SELL' if ch <= -2 else 'WAIT'
    candidates = sorted(usable, key=lambda x: (abs(x['change']), x['volume']), reverse=True)[:30]
    with ThreadPoolExecutor(max_workers=5) as ex:
        fs = {ex.submit(technical, x['symbol']): x for x in candidates}
        for f in as_completed(fs):
            try:
                t = f.result()
                if t:
                    fs[f].update(t)
            except Exception:
                pass
    rows.sort(key=lambda x: x['score'], reverse=True)
    STATE['rows'] = rows
    STATE['ts'] = time.time()
    return rows


def alert_once(key, text):
    if any(x.get('key') == key for x in STATE['signals']):
        return False
    ok = telegram(text)
    STATE['signals'].append({'key': key, 'time': now_ist().strftime('%H:%M:%S'), 'text': text, 'sent': ok})
    STATE['signals'] = STATE['signals'][-100:]
    return ok


def monitor_positions(rows):
    by = {x['symbol']: x for x in rows if not x.get('error')}
    for p in STATE['positions']:
        if p.get('status') != 'OPEN':
            continue
        r = by.get(p['symbol'])
        if not r:
            continue
        price = r['price']
        qty = float(p['qty'])
        entry = float(p['entry'])
        side = p.get('side', 'LONG')
        pnl = (price - entry) * qty if side == 'LONG' else (entry - price) * qty
        p['ltp'] = price
        p['pnl'] = round(pnl, 2)
        hit_target = price >= float(p['target']) if side == 'LONG' else price <= float(p['target'])
        hit_sl = price <= float(p['sl']) if side == 'LONG' else price >= float(p['sl'])
        if hit_target or hit_sl:
            reason = 'TARGET HIT' if hit_target else 'STOP LOSS HIT'
            p['status'] = 'EXIT SIGNAL'
            key = f"exit_{p['symbol']}_{reason}_{p['entry']}"
            alert_once(key, f"🔔 {reason}\n{p['symbol']}\nEntry ₹{entry:.2f}\nLTP ₹{price:.2f}\nQty {int(qty)}\nP&L ₹{pnl:.2f}")


def auto_cycle():
    try:
        rows = scan()
        if market_state() == 'OPEN':
            s = load_settings()
            if s.get('auto', True):
                buys = [x for x in rows if x['signal'] == 'BUY' and x['score'] >= 78]
                sells = [x for x in rows if x['signal'] == 'SELL' and x['score'] <= 22]
                if buys:
                    x = buys[0]
                    alert_once('buy_' + x['symbol'], f"🟢 BUY NOW\n{x['symbol']}\nPrice ₹{x['price']}\nScore {x['score']}\nTarget ₹{x['price']*1.015:.2f}\nStop Loss ₹{x['price']*.99:.2f}")
                if sells:
                    x = sells[0]
                    alert_once('short_' + x['symbol'], f"🔴 SELL SETUP\n{x['symbol']}\nPrice ₹{x['price']}\nScore {x['score']}\nTarget ₹{x['price']*.985:.2f}\nStop Loss ₹{x['price']*1.01:.2f}")
        monitor_positions(rows)
        STATE['last_auto'] = now_ist().strftime('%I:%M:%S %p')
    except Exception as e:
        STATE['last_auto'] = 'error: ' + str(e)[:100]


def auto_loop():
    while True:
        auto_cycle()
        time.sleep(60)


HTML = '''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Intraday AI</title><style>*{box-sizing:border-box}body{margin:0;background:#f3f5f8;color:#172033;font-family:Arial,sans-serif}header{background:#101827;color:#fff;padding:20px 28px}h1{margin:0}.sub{opacity:.7;margin-top:5px}.status,.controls{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.status{margin-top:12px;background:#1d2939;padding:12px;border-radius:10px}.wrap{max-width:1500px;margin:auto;padding:18px}.panel{background:#fff;border-radius:14px;padding:18px;margin-bottom:16px;box-shadow:0 2px 12px #0001}input,button,select{padding:10px 12px;border:1px solid #ccd3dd;border-radius:8px}button{background:#111827;color:#fff;font-weight:800;cursor:pointer}.cards{display:grid;grid-template-columns:repeat(6,1fr);gap:10px}.card{background:#f7f8fa;padding:13px;border-radius:10px}.num{display:block;font-size:21px;font-weight:800;margin-top:5px}.buy{color:#07833a}.sell{color:#c62828}.wait{color:#697386}.auto{color:#07833a;font-weight:800}table{width:100%;border-collapse:collapse}th,td{padding:8px 7px;border-bottom:1px solid #edf0f4;text-align:left;white-space:nowrap}th{font-size:11px;color:#697386}.small{font-size:12px;color:#697386}.tag{padding:4px 8px;border-radius:12px;background:#edf2f7;font-size:12px}@media(max-width:900px){.cards{grid-template-columns:repeat(3,1fr)}}@media(max-width:600px){.cards{grid-template-columns:repeat(2,1fr)}} </style></head><body><header><h1>Intraday AI</h1><div class="sub">NSE complete equity scanner • automatic monitoring • BUY / WAIT / SELL • position alerts</div><div class="status"><span id="clock">🕐 --</span><span id="date">📅 --</span><span id="market">MARKET --</span><span id="last">Last update: —</span><span id="autoLast">Auto: —</span></div></header><div class="wrap"><div class="panel"><div class="controls"><b>🤖 AUTO SYSTEM</b><span id="autoState" class="auto">ON</span><button onclick="toggleAuto()">AUTO ON/OFF</button><span class="small">Automatic scan, signal alerts, target/SL monitoring and P&L tracking. No automatic order placement.</span></div></div><div class="panel"><div class="controls"><b>Telegram</b><input id="bot" placeholder="Bot Token" style="min-width:220px"><input id="chat" placeholder="Chat ID"><button onclick="saveSettings()">SAVE ALERTS</button><button onclick="testAlert()">TEST PHONE</button></div></div><div class="panel"><div class="controls"><b>Budget ₹</b><input id="budget" type="number" value="5000"><b>Max Loss/Trade ₹</b><input id="risk" type="number" value="500"><b>Max Trades</b><input id="maxtrades" type="number" value="2"><button onclick="scan()">SCAN NSE</button><span id="msg">Starting…</span></div></div><div class="panel"><div class="cards"><div class="card">NSE Stocks<span id="stocks" class="num">0</span></div><div class="card">BUY<span id="buys" class="num buy">0</span></div><div class="card">SELL<span id="sells" class="num sell">0</span></div><div class="card">WAIT<span id="waits" class="num wait">0</span></div><div class="card">Best Score<span id="topscore" class="num">—</span></div><div class="card">Data Source<span id="source" class="num">NSE</span></div></div></div><div class="panel"><b>🤖 TRADING GUIDE</b><div id="guide">Automatic system is starting…</div></div><div class="panel"><h3>📌 My Position — Automatic Monitor</h3><div class="controls"><input id="psymbol" placeholder="Stock e.g. RELIANCE"><select id="pside"><option>LONG</option><option>SHORT</option></select><input id="pentry" type="number" placeholder="Entry Price"><input id="pqty" type="number" placeholder="Qty"><input id="ptarget" type="number" placeholder="Target"><input id="psl" type="number" placeholder="Stop Loss"><button onclick="addPosition()">ADD POSITION</button></div><div class="small">Once added, current price and P&L are monitored automatically and Telegram alerts are sent on target/SL.</div><div id="positions"></div></div><div class="panel"><h3>NSE Equity Scanner</h3><div style="overflow:auto"><table><thead><tr><th>Stock</th><th>Price</th><th>Change</th><th>RSI</th><th>EMA9</th><th>EMA21</th><th>Volume</th><th>Score</th><th>Signal</th><th>Budget Qty</th><th>Risk Qty</th><th>Target</th><th>SL</th></tr></thead><tbody id="rows"></tbody></table></div></div><div class="panel small">Market hours follow NSE regular equity trading: 09:15–15:30 IST on trading days. Signals are informational and do not guarantee profit. Automatic order placement is intentionally disabled.</div></div><script>let busy=false;function tick(){let d=new Date();clock.textContent='🕐 '+d.toLocaleTimeString('en-IN',{hour12:true});date.textContent='📅 '+d.toLocaleDateString('en-IN');fetch('/api/state').then(r=>r.json()).then(s=>{market.textContent='MARKET '+s.market;autoLast.textContent='Auto: '+(s.last_auto||'—');if(s.settings)autoState.textContent=s.settings.auto?'ON':'OFF';if(s.positions)renderPositions(s.positions)}).catch(()=>{})}setInterval(tick,5000);tick();function saveSettings(){fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({bot_token:bot.value.trim(),chat_id:chat.value.trim(),alerts:true,budget:+budget.value,risk:+risk.value,max_trades:+maxtrades.value})}).then(r=>r.json()).then(x=>msg.textContent=x.ok?'Alerts saved':'Save failed').catch(()=>msg.textContent='Save failed')}function toggleAuto(){fetch('/api/auto',{method:'POST'}).then(r=>r.json()).then(x=>{autoState.textContent=x.auto?'ON':'OFF';msg.textContent=x.auto?'Automatic system ON':'Automatic system OFF'})}function testAlert(){fetch('/api/test').then(r=>r.json()).then(x=>msg.textContent=x.ok?'Telegram test sent':'Telegram test failed')}function qty(p){let b=+budget.value||0,r=+risk.value||0;return{b:Math.floor(b/p),r:Math.floor(r/(p*.01))}}function render(a){let ok=a.filter(x=>!x.error);stocks.textContent=ok.length;buys.textContent=ok.filter(x=>x.signal==='BUY').length;sells.textContent=ok.filter(x=>x.signal==='SELL').length;waits.textContent=ok.filter(x=>x.signal==='WAIT').length;topscore.textContent=ok.length?ok[0].score:'—';let rr=ok.slice().sort((x,y)=>y.score-x.score);rows.innerHTML=rr.map(x=>{let q=qty(x.price);return `<tr><td><b>${x.symbol}</b></td><td>₹${x.price}</td><td>${x.change}%</td><td>${x.rsi??'—'}</td><td>${x.ema9??'—'}</td><td>${x.ema21??'—'}</td><td>${x.volume||0}</td><td>${x.score}</td><td class="${x.signal.toLowerCase()}"><b>${x.signal}</b></td><td>${q.b}</td><td>${q.r}</td><td>₹${(x.price*1.015).toFixed(2)}</td><td>₹${(x.price*.99).toFixed(2)}</td></tr>`}).join('');if(ok[0])guide.innerHTML=`Top setup: <b>${ok[0].symbol}</b> — ${ok[0].signal}, score ${ok[0].score}. Auto system is monitoring.`;else guide.textContent='NSE data unavailable; no trading signal.'}async function scan(){if(busy)return;busy=true;msg.textContent='Scanning NSE…';try{let r=await fetch('/api/scan?force=1');let d=await r.json();if(!d.ok){msg.textContent=d.error||'NSE unavailable';return}render(d.rows);last.textContent='Last update: '+d.time;msg.textContent='Scanned '+d.scanned+' NSE stocks'}catch(e){msg.textContent='NSE scan failed'}finally{busy=false}}function addPosition(){let x={symbol:psymbol.value.trim().toUpperCase(),side:pside.value,entry:+pentry.value,qty:+pqty.value,target:+ptarget.value,sl:+psl.value};if(!x.symbol||!x.entry||!x.qty||!x.target||!x.sl){msg.textContent='Fill all position fields';return}fetch('/api/positions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(x)}).then(r=>r.json()).then(x=>{msg.textContent=x.ok?'Position added':'Position failed';tick()})}function renderPositions(a){positions.innerHTML=a.length?a.map((p,i)=>`<div style="margin-top:10px;padding:10px;background:#f7f8fa;border-radius:8px"><b>${p.symbol}</b> ${p.side} • Qty ${p.qty} • Entry ₹${p.entry} • LTP ₹${p.ltp??'—'} • P&L ₹${p.pnl??'—'} • Target ₹${p.target} • SL ₹${p.sl} • <span class="tag">${p.status}</span> <button onclick="closePos(${i})">CLOSE</button></div>`).join(''):'No open positions.'}function closePos(i){fetch('/api/positions/'+i,{method:'DELETE'}).then(()=>tick())}setInterval(scan,60000);scan();</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def send_json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        try:
            if self.path == '/' or self.path.startswith('/?'):
                b = HTML.encode(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b); return
            if self.path.startswith('/health'):
                self.send_json({'ok': True, 'service':'Intraday AI Auto', 'source':'NSE'}); return
            if self.path.startswith('/api/state'):
                self.send_json({'ok':True,'market':market_state(),'last_auto':STATE['last_auto'],'positions':STATE['positions'],'settings':{'auto':load_settings().get('auto',True)}}); return
            if self.path.startswith('/api/scan'):
                rows = scan('force=1' in self.path); self.send_json({'ok':True,'rows':rows,'scanned':len(rows),'market':market_state(),'time':now_ist().strftime('%I:%M:%S %p')}); return
            if self.path.startswith('/api/test'):
                self.send_json({'ok':telegram('🟢 Intraday AI automatic system test — Telegram connection is working.')}); return
            self.send_json({'error':'not found'},404)
        except Exception as e: self.send_json({'ok':False,'error':str(e)[:200]},500)
    def do_POST(self):
        try:
            if self.path.startswith('/api/settings'):
                n=int(self.headers.get('Content-Length','0')); x=json.loads(self.rfile.read(n) or b'{}'); save_settings(x); self.send_json({'ok':True}); return
            if self.path.startswith('/api/auto'):
                s=load_settings(); save_settings({'auto':not bool(s.get('auto',True))}); self.send_json({'ok':True,'auto':not bool(s.get('auto',True))}); return
            if self.path.startswith('/api/positions'):
                n=int(self.headers.get('Content-Length','0')); x=json.loads(self.rfile.read(n) or b'{}'); x['status']='OPEN'; x['created']=now_ist().isoformat(); STATE['positions'].append(x); self.send_json({'ok':True}); return
            self.send_json({'error':'not found'},404)
        except Exception as e: self.send_json({'ok':False,'error':str(e)[:200]},400)
    def do_DELETE(self):
        try:
            if self.path.startswith('/api/positions/'):
                i=int(self.path.rsplit('/',1)[-1]);
                if 0 <= i < len(STATE['positions']): STATE['positions'][i]['status']='CLOSED'
                self.send_json({'ok':True}); return
            self.send_json({'error':'not found'},404)
        except Exception as e: self.send_json({'ok':False,'error':str(e)[:200]},400)
    def log_message(self,*args): pass


if __name__ == '__main__':
    threading.Thread(target=auto_loop, daemon=True).start()
    port = int(os.environ.get('PORT','10000'))
    ThreadingHTTPServer(('0.0.0.0',port),Handler).serve_forever()
