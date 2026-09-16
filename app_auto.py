import json, os, threading, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

from nsemine import live

DATA_DIR = os.environ.get('DATA_DIR', '/tmp/intraday_ai_data')
os.makedirs(DATA_DIR, exist_ok=True)
SETTINGS_FILE = os.path.join(DATA_DIR, 'auto_settings.json')

STATE = {
    'rows': [], 'ts': 0, 'positions': [], 'signals': [],
    'last_auto': '', 'last_error': '', 'scanning': False,
    'lock': threading.Lock(), 'technical_cache': {}
}
SCAN_TTL = 75
TECH_TTL = 240


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
        url = 'https://api.telegram.org/bot' + str(s['bot_token']) + '/sendMessage'
        data = urllib.parse.urlencode({'chat_id': s['chat_id'], 'text': text}).encode()
        req = urllib.request.Request(url, data=data, headers={'User-Agent': 'IntradayAI/2'})
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
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def technical(symbol):
    cached = STATE['technical_cache'].get(symbol)
    if cached and time.time() - cached['ts'] < TECH_TTL:
        return cached['data']
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
        bull += 25 if p > e9 else 0
        bear += 25 if p <= e9 else 0
        bull += 25 if p > e21 else 0
        bear += 25 if p <= e21 else 0
        bull += 20 if e9 > e21 else 0
        bear += 20 if e9 <= e21 else 0
        if rr is not None:
            bull += 15 if 52 <= rr <= 68 else 0
            bear += 15 if 32 <= rr <= 48 else 0
        bull += 15 if mom > 0 else 0
        bear += 15 if mom < 0 else 0
        score = max(0, min(100, 50 + bull - bear))
        signal = 'BUY' if bull >= 70 and bull - bear >= 30 else 'SELL' if bear >= 70 and bear - bull >= 30 else 'WAIT'
        data = {'price': round(p, 2), 'rsi': round(rr, 1) if rr is not None else None,
                'ema9': round(e9, 2), 'ema21': round(e21, 2), 'momentum': round(mom, 3),
                'score': round(score, 1), 'signal': signal}
        STATE['technical_cache'][symbol] = {'ts': time.time(), 'data': data}
        return data
    except Exception:
        return None


def get_snapshot():
    last_error = ''
    for attempt in range(2):
        try:
            df = live.get_all_securities_live_snapshot(series='EQ')
            if df is not None and len(df):
                return df
            last_error = 'NSE returned no live rows'
        except Exception as e:
            last_error = str(e)[:140]
        if attempt == 0:
            time.sleep(2)
    raise RuntimeError(last_error or 'NSE live market data unavailable')


def scan():
    if STATE['rows'] and time.time() - STATE['ts'] < SCAN_TTL:
        return STATE['rows']
    with STATE['lock']:
        if STATE['rows'] and time.time() - STATE['ts'] < SCAN_TTL:
            return STATE['rows']
        STATE['scanning'] = True
        try:
            live_df = get_snapshot().copy()
            live_df['symbol'] = live_df['symbol'].astype(str).str.strip()
            live_df = live_df.drop_duplicates('symbol')
            rows = []
            for _, r in live_df.iterrows():
                try:
                    sym = str(r['symbol']).strip()
                    price = float(r['close'])
                    change = float(r['changepct'])
                    volume = int(float(r['volume'])) if r.get('volume') is not None else 0
                    rows.append({'symbol': sym, 'price': round(price, 2), 'change': round(change, 2),
                                 'volume': volume, 'score': 50, 'signal': 'WAIT', 'rsi': None,
                                 'ema9': None, 'ema21': None, 'momentum': None, 'source': 'NSE'})
                except Exception:
                    continue
            if not rows:
                raise RuntimeError('NSE live market data had no usable equity rows')
            max_vol = max((x['volume'] for x in rows), default=1) or 1
            for x in rows:
                ch = x['change']
                x['score'] = round(max(0, min(100, 50 + max(-25, min(25, ch * 8)) + min(10, x['volume'] / max_vol * 10))), 1)
                x['signal'] = 'BUY' if ch >= 2 else 'SELL' if ch <= -2 else 'WAIT'
            candidates = sorted(rows, key=lambda x: (abs(x['change']), x['volume']), reverse=True)[:10]
            with ThreadPoolExecutor(max_workers=3) as ex:
                fs = {ex.submit(technical, x['symbol']): x for x in candidates}
                for f in as_completed(fs):
                    try:
                        t = f.result()
                        if t:
                            fs[f].update(t)
                    except Exception:
                        pass
            rows.sort(key=lambda x: x['score'], reverse=True)
            STATE['rows'], STATE['ts'], STATE['last_error'] = rows, time.time(), ''
            return rows
        except Exception as e:
            STATE['last_error'] = str(e)[:160]
            if STATE['rows']:
                return STATE['rows']
            raise
        finally:
            STATE['scanning'] = False


def alert_once(key, text):
    if any(x.get('key') == key for x in STATE['signals']):
        return False
    ok = telegram(text)
    STATE['signals'].append({'key': key, 'time': now_ist().strftime('%H:%M:%S'), 'text': text, 'sent': ok})
    STATE['signals'] = STATE['signals'][-100:]
    return ok


def monitor_positions(rows):
    by = {x['symbol']: x for x in rows}
    for p in STATE['positions']:
        if p.get('status') != 'OPEN' or p.get('symbol') not in by:
            continue
        r = by[p['symbol']]
        price, qty, entry = r['price'], float(p['qty']), float(p['entry'])
        side = p.get('side', 'LONG')
        pnl = (price - entry) * qty if side == 'LONG' else (entry - price) * qty
        p['ltp'], p['pnl'] = price, round(pnl, 2)
        target = float(p['target']); sl = float(p['sl'])
        hit_target = price >= target if side == 'LONG' else price <= target
        hit_sl = price <= sl if side == 'LONG' else price >= sl
        if hit_target or hit_sl:
            reason = 'TARGET HIT' if hit_target else 'STOP LOSS HIT'
            p['status'] = 'EXIT SIGNAL'
            alert_once(f"exit_{p['symbol']}_{p['entry']}_{reason}",
                       f"🔔 {reason}\n{p['symbol']}\nEntry ₹{entry:.2f}\nLTP ₹{price:.2f}\nQty {int(qty)}\nP&L ₹{pnl:.2f}")


def auto_cycle():
    try:
        rows = scan()
        if market_state() == 'OPEN' and load_settings().get('auto', True):
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
    time.sleep(3)
    while True:
        auto_cycle()
        time.sleep(90)


HTML = '''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Intraday AI</title><style>*{box-sizing:border-box}body{margin:0;background:#f3f5f8;color:#172033;font-family:Arial,sans-serif}header{background:#101827;color:#fff;padding:20px 28px}h1{margin:0}.sub{opacity:.7;margin-top:5px}.status,.controls{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.status{margin-top:12px;background:#1d2939;padding:12px;border-radius:10px}.wrap{max-width:1500px;margin:auto;padding:18px}.panel{background:#fff;border-radius:14px;padding:18px;margin-bottom:16px;box-shadow:0 2px 12px #0001}input,button,select{padding:10px 12px;border:1px solid #ccd3dd;border-radius:8px}button{background:#111827;color:#fff;font-weight:800;cursor:pointer}.cards{display:grid;grid-template-columns:repeat(6,1fr);gap:10px}.card{background:#f7f8fa;padding:13px;border-radius:10px}.num{display:block;font-size:21px;font-weight:800;margin-top:5px}.buy{color:#07833a}.sell{color:#c62828}.wait{color:#697386}.auto{color:#07833a;font-weight:800}table{width:100%;border-collapse:collapse}th,td{padding:8px 7px;border-bottom:1px solid #edf0f4;text-align:left;white-space:nowrap}th{font-size:11px;color:#697386}.small{font-size:12px;color:#697386}.tag{padding:4px 8px;border-radius:12px;background:#edf2f7;font-size:12px}@media(max-width:900px){.cards{grid-template-columns:repeat(3,1fr)}}@media(max-width:600px){.cards{grid-template-columns:repeat(2,1fr)}table{font-size:11px}}</style></head><body><header><h1>Intraday AI</h1><div class="sub">NSE equity scanner • automatic monitoring • BUY / WAIT / SELL • position alerts</div><div class="status"><span id="clock">🕐 --</span><span id="date">📅 --</span><span id="market">MARKET --</span><span id="last">Last update: —</span><span id="autoLast">Auto: —</span></div></header><div class="wrap"><div class="panel"><div class="controls"><b>🤖 AUTO SYSTEM</b><span id="autoState" class="auto">ON</span><button onclick="toggleAuto()">AUTO ON/OFF</button><span class="small">Automatic scan, alerts, target/SL monitoring and P&L tracking. No automatic order placement.</span></div></div><div class="panel"><div class="controls"><b>Phone Alerts (Telegram)</b><input id="bot" placeholder="Bot Token" style="min-width:220px"><input id="chat" placeholder="Chat ID"><button onclick="saveSettings()">SAVE ALERTS</button><button onclick="testAlert()">TEST PHONE</button></div><div class="small">NSE data source. Keep this page open during trading hours for browser-driven scans and alerts.</div></div><div class="panel"><div class="controls"><b>Investment Budget ₹</b><input id="budget" type="number" value="5000"><b>Max Loss/Trade ₹</b><input id="risk" type="number" value="500"><b>Max Trades</b><input id="maxtrades" type="number" value="2"><button onclick="scanNow()">SCAN NSE</button><span id="msg">Starting…</span></div></div><div class="panel"><div class="cards"><div class="card">NSE Stocks<span id="stocks" class="num">0</span></div><div class="card">BUY<span id="buys" class="num buy">0</span></div><div class="card">SELL<span id="sells" class="num sell">0</span></div><div class="card">WAIT<span id="waits" class="num wait">0</span></div><div class="card">Best Score<span id="topscore" class="num">—</span></div><div class="card">Data Source<span id="source" class="num">NSE</span></div></div></div><div class="panel"><b>🤖 TRADING GUIDE</b><div id="guide">Live scanner is starting. Strong signals are only alerts, not guaranteed outcomes.</div></div><div class="panel"><h3>📌 My Position — Automatic Monitor</h3><div class="controls"><input id="psymbol" placeholder="Stock e.g. RELIANCE"><select id="pside"><option>LONG</option><option>SHORT</option></select><input id="pentry" type="number" placeholder="Entry Price"><input id="pqty" type="number" placeholder="Qty"><input id="ptarget" type="number" placeholder="Target"><input id="psl" type="number" placeholder="Stop Loss"><button onclick="addPosition()">ADD POSITION</button></div><div id="positions" class="small" style="margin-top:12px">No open positions.</div></div><div class="panel"><h3>📊 NSE Equity Scanner</h3><div style="overflow:auto"><table><thead><tr><th>Stock</th><th>Price</th><th>Change</th><th>RSI</th><th>EMA9</th><th>EMA21</th><th>Volume</th><th>Score</th><th>Signal</th><th>Budget Qty</th><th>Risk Qty</th><th>Plan Qty</th><th>Target</th><th>SL</th></tr></thead><tbody id="rows"></tbody></table></div></div><div class="panel small">Primary market data: NSE via nsemine. NSE access can be rate-limited temporarily. Cached data is retained when a refresh fails. This scanner does not guarantee profit and does not place orders automatically.</div></div><script>const $=id=>document.getElementById(id);function fmt(x){return x==null?'—':x}function money(x){return '₹'+Number(x).toFixed(2)}function tick(){let d=new Date();$('clock').textContent='🕐 '+d.toLocaleTimeString('en-IN');$('date').textContent='📅 '+d.toLocaleDateString('en-IN');$('market').textContent='MARKET '+(d.getDay()==0||d.getDay()==6?'CLOSED':(d.getHours()*60+d.getMinutes()<555?'PRE-OPEN':d.getHours()*60+d.getMinutes()<=930?'OPEN':'CLOSED'))}setInterval(tick,1000);tick();async function state(){try{let r=await fetch('/api/state');let j=await r.json();render(j);$('autoState').textContent=j.settings.auto?'ON':'OFF';$('autoLast').textContent='Auto: '+(j.last_auto||'—');}catch(e){}}function render(j){let rows=j.rows||[];$('stocks').textContent=rows.length;$('buys').textContent=rows.filter(x=>x.signal==='BUY').length;$('sells').textContent=rows.filter(x=>x.signal==='SELL').length;$('waits').textContent=rows.filter(x=>x.signal==='WAIT').length;$('topscore').textContent=rows.length?rows[0].score:'—';$('source').textContent='NSE';$('last').textContent='Last update: '+(j.ts?new Date(j.ts*1000).toLocaleTimeString('en-IN'):'—');$('msg').textContent=j.error?('NSE refresh failed — using cached data'):j.scanning?'Scanning NSE…':'Live NSE data';let budget=Number($('budget').value||5000),risk=Number($('risk').value||500);$('rows').innerHTML=rows.slice(0,300).map(x=>{let b=x.price>0?Math.floor(budget/x.price):0;let rq=x.price>0?Math.floor(risk/(x.price*.01)):0;let q=Math.max(0,Math.min(b,rq||b));return '<tr><td><b>'+x.symbol+'</b></td><td>'+money(x.price)+'</td><td>'+x.change+'%</td><td>'+fmt(x.rsi)+'</td><td>'+fmt(x.ema9)+'</td><td>'+fmt(x.ema21)+'</td><td>'+Number(x.volume).toLocaleString('en-IN')+'</td><td>'+x.score+'</td><td><span class="tag">'+x.signal+'</span></td><td>'+b+'</td><td>'+rq+'</td><td>'+q+'</td><td>'+money(x.price*1.015)+'</td><td>'+money(x.price*.99)+'</td></tr>'}).join('');$('guide').textContent=rows.length?'Automatic system scans NSE with a low-frequency refresh to reduce rate limits. Strong BUY/SELL alerts are sent once per stock.':'Waiting for NSE data…';let ps=j.positions||[];$('positions').innerHTML=ps.length?ps.map((p,i)=>p.symbol+' | '+p.side+' | Entry '+money(p.entry)+' | LTP '+money(p.ltp||p.entry)+' | Qty '+p.qty+' | P&L '+money(p.pnl||0)+' | <b>'+p.status+'</b> <button onclick="delPosition('+i+')">REMOVE</button>').join('<br>'):'No open positions.'}async function scanNow(){ $('msg').textContent='Refreshing NSE…';try{await fetch('/api/scan');}catch(e){}state()}async function saveSettings(){await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({bot_token:$('bot').value,chat_id:$('chat').value,alerts:true,budget:Number($('budget').value),risk:Number($('risk').value),max_trades:Number($('maxtrades').value)})});$('msg').textContent='Alerts saved'}async function testAlert(){let r=await fetch('/api/test');let j=await r.json();$('msg').textContent=j.ok?'Test sent':'Test failed — check Telegram token/chat ID'}async function toggleAuto(){let r=await fetch('/api/auto',{method:'POST'});let j=await r.json();$('autoState').textContent=j.auto?'ON':'OFF'}async function addPosition(){let body={symbol:$('psymbol').value.trim().toUpperCase(),side:$('pside').value,entry:Number($('pentry').value),qty:Number($('pqty').value),target:Number($('ptarget').value),sl:Number($('psl').value)};await fetch('/api/positions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});$('psymbol').value='';state()}async function delPosition(i){await fetch('/api/positions/'+i,{method:'DELETE'});state()}setInterval(state,15000);state();</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        return
    def send_json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
    def body(self):
        n = int(self.headers.get('Content-Length','0')); return json.loads(self.rfile.read(n) or b'{}')
    def do_GET(self):
        if self.path == '/health':
            self.send_json({'ok': True, 'time': now_ist().isoformat()}); return
        if self.path == '/api/state':
            s=load_settings(); self.send_json({'rows':STATE['rows'],'ts':STATE['ts'],'positions':STATE['positions'],'signals':STATE['signals'][-20:],'last_auto':STATE['last_auto'],'error':STATE['last_error'],'scanning':STATE['scanning'],'settings':{'auto':s.get('auto',True)}}); return
        if self.path == '/api/scan':
            try: rows=scan(); self.send_json({'ok':True,'count':len(rows),'ts':STATE['ts']})
            except Exception as e: self.send_json({'ok':False,'error':str(e)},503)
            return
        if self.path == '/api/test':
            ok=telegram('✅ Intraday AI test alert — Telegram is connected.'); self.send_json({'ok':ok}); return
        self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(HTML.encode()))); self.end_headers(); self.wfile.write(HTML.encode())
    def do_POST(self):
        if self.path == '/api/settings':
            s=self.body(); s.pop('bot_token',None) if not s.get('bot_token') else None
            save_settings(self.body() if False else s); self.send_json({'ok':True}); return
        if self.path == '/api/auto':
            s=load_settings(); s['auto']=not s.get('auto',True); save_settings({'auto':s['auto']}); self.send_json({'auto':s['auto']}); return
        if self.path == '/api/positions':
            p=self.body();
            try:
                p['symbol']=str(p['symbol']).upper().strip(); p['entry']=float(p['entry']); p['qty']=int(p['qty']); p['target']=float(p['target']); p['sl']=float(p['sl']); p['status']='OPEN'; p['ltp']=p['entry']; p['pnl']=0
                STATE['positions'].append(p); self.send_json({'ok':True})
            except Exception as e: self.send_json({'ok':False,'error':str(e)},400)
            return
        self.send_json({'ok':False},404)
    def do_DELETE(self):
        if self.path.startswith('/api/positions/'):
            try:
                i=int(self.path.rsplit('/',1)[1]); STATE['positions'].pop(i); self.send_json({'ok':True})
            except Exception as e: self.send_json({'ok':False,'error':str(e)},400)
            return
        self.send_json({'ok':False},404)


if __name__ == '__main__':
    threading.Thread(target=auto_loop, daemon=True).start()
    port=int(os.environ.get('PORT','10000'))
    ThreadingHTTPServer(('0.0.0.0',port),Handler).serve_forever()
