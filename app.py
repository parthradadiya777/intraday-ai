import json, os, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

from nsemine import live, nse

DATA_DIR = os.environ.get('DATA_DIR', '/tmp/intraday_ai_data')
os.makedirs(DATA_DIR, exist_ok=True)
SETTINGS_FILE = os.path.join(DATA_DIR, 'guide_settings.json')
CACHE = {'ts': 0, 'rows': [], 'universe': []}
LAST_ALERT = {}


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
        return {'bot_token': '', 'chat_id': '', 'alerts': True}


def save_settings(x):
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(x, f)


def telegram(text):
    s = load_settings()
    if not s.get('alerts') or not s.get('bot_token') or not s.get('chat_id'):
        return False
    try:
        u = 'https://api.telegram.org/bot' + s['bot_token'] + '/sendMessage'
        d = urllib.parse.urlencode({'chat_id': s['chat_id'], 'text': text}).encode()
        r = urllib.request.urlopen(urllib.request.Request(u, data=d, headers={'User-Agent': 'IntradayAI'}), timeout=8)
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
        close_col = cols.get('close') or cols.get('last')
        if not close_col:
            return None
        closes = [float(x) for x in df[close_col].tolist() if x is not None]
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
            if 32 <= rr <= 48: bear += 15
        if mom > 0: bull += 15
        elif mom < 0: bear += 15
        score = max(0, min(100, 50 + bull - bear))
        signal = 'WAIT'
        if bull >= 70 and bull - bear >= 30: signal = 'BUY'
        elif bear >= 70 and bear - bull >= 30: signal = 'SELL'
        return {
            'price': round(p, 2), 'rsi': round(rr, 1) if rr is not None else None,
            'ema9': round(e9, 2), 'ema21': round(e21, 2), 'momentum': round(mom, 3),
            'score': round(score, 1), 'signal': signal, 'detail': True
        }
    except Exception:
        return None


def base_signal(change, volume_rank):
    # Safe market-wide signal when intraday candles are not fetched for every stock.
    ch = float(change or 0)
    score = 50 + max(-25, min(25, ch * 8)) + min(10, volume_rank)
    if ch >= 2.0:
        signal = 'BUY'
    elif ch <= -2.0:
        signal = 'SELL'
    else:
        signal = 'WAIT'
    return round(max(0, min(100, score)), 1), signal


def get_universe():
    # NSE master list; fallback to the live NSE snapshot if the master endpoint is unavailable.
    try:
        df = nse.get_all_equities_list()
        if df is not None and len(df):
            df = df[df['series'].astype(str).str.upper().eq('EQ')]
            return sorted(set(df['symbol'].astype(str).str.strip()))
    except Exception:
        pass
    return []


def scan(force=False):
    import time
    if not force and CACHE['rows'] and time.time() - CACHE['ts'] < 45:
        return CACHE['rows']

    live_df = live.get_all_securities_live_snapshot(series='EQ')
    if live_df is None or len(live_df) == 0:
        raise RuntimeError('NSE live market data unavailable')

    universe = get_universe()
    if not universe:
        universe = sorted(set(live_df['symbol'].astype(str).str.strip()))
    CACHE['universe'] = universe

    live_df = live_df.copy()
    live_df['symbol'] = live_df['symbol'].astype(str).str.strip()
    live_df = live_df.drop_duplicates('symbol')
    live_map = {r['symbol']: r for _, r in live_df.iterrows()}

    rows = []
    for sym in universe:
        r = live_map.get(sym)
        if r is None:
            rows.append({'symbol': sym, 'error': True, 'price': None, 'change': None, 'score': 0, 'signal': 'WAIT', 'source': 'NSE'})
            continue
        try:
            price = float(r['close'])
            change = float(r['changepct'])
            volume = int(r['volume']) if r.get('volume') is not None else 0
            rows.append({'symbol': sym, 'price': round(price, 2), 'change': round(change, 2), 'volume': volume,
                         'score': 50, 'signal': 'WAIT', 'rsi': None, 'ema9': None, 'ema21': None,
                         'momentum': None, 'vwap': None, 'detail': False, 'source': 'NSE', 'error': False})
        except Exception:
            rows.append({'symbol': sym, 'error': True, 'price': None, 'change': None, 'score': 0, 'signal': 'WAIT', 'source': 'NSE'})

    usable = [x for x in rows if not x.get('error')]
    max_vol = max([x.get('volume', 0) for x in usable] or [1])
    for x in usable:
        x['score'], x['signal'] = base_signal(x.get('change', 0), (x.get('volume', 0) / max_vol) * 10)

    # Fetch true 5-minute NSE candles only for the most liquid/active 30 names.
    candidates = sorted(usable, key=lambda x: (abs(x.get('change', 0)), x.get('volume', 0)), reverse=True)[:30]
    with ThreadPoolExecutor(max_workers=5) as ex:
        fs = {ex.submit(technical, x['symbol']): x for x in candidates}
        for f in as_completed(fs):
            t = f.result()
            x = fs[f]
            if t:
                x.update(t)
                x['source'] = 'NSE'

    if market_state() == 'OPEN':
        buys = sorted([x for x in usable if x['signal'] == 'BUY'], key=lambda x: x['score'], reverse=True)
        if buys and buys[0]['score'] >= 78:
            x = buys[0]
            key = 'buy_' + x['symbol'] + '_' + str(x['score'])
            if not LAST_ALERT.get(key) and telegram(f"🟢 BUY NOW — {x['symbol']}\nPrice ₹{x['price']}\nScore {x['score']}\nTarget ₹{x['price']*1.015:.2f}\nStop Loss ₹{x['price']*.99:.2f}"):
                LAST_ALERT[key] = 1

    CACHE['rows'], CACHE['ts'] = rows, time.time()
    return rows


HTML = '''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Intraday AI</title><style>*{box-sizing:border-box}body{margin:0;background:#f3f5f8;color:#172033;font-family:Arial,sans-serif}header{background:#101827;color:#fff;padding:20px 28px}h1{margin:0}.sub{opacity:.7;margin-top:5px}.status,.controls{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.status{margin-top:12px;background:#1d2939;padding:12px;border-radius:10px}.wrap{max-width:1500px;margin:auto;padding:18px}.panel{background:#fff;border-radius:14px;padding:18px;margin-bottom:16px;box-shadow:0 2px 12px #0001}input,button,select{padding:10px 12px;border:1px solid #ccd3dd;border-radius:8px}button{background:#111827;color:#fff;font-weight:800;cursor:pointer}.cards{display:grid;grid-template-columns:repeat(6,1fr);gap:10px}.card{background:#f7f8fa;padding:13px;border-radius:10px}.num{display:block;font-size:21px;font-weight:800;margin-top:5px}.buy{color:#07833a}.sell{color:#c62828}.wait{color:#697386}table{width:100%;border-collapse:collapse}th,td{padding:8px 7px;border-bottom:1px solid #edf0f4;text-align:left;white-space:nowrap}th{font-size:11px;color:#697386}.small{font-size:12px;color:#697386}.error{color:#9a3412}@media(max-width:900px){.cards{grid-template-columns:repeat(3,1fr)}}@media(max-width:600px){.cards{grid-template-columns:repeat(2,1fr)}}</style></head><body><header><h1>Intraday AI</h1><div class="sub">NSE complete equity scanner • live price • volume • 5-min technical confirmation</div><div class="status"><span id="clock">🕐 --</span><span id="date">📅 --</span><span id="market">MARKET --</span><span id="last">Last update: —</span><span id="next">Next scan: —</span></div></header><div class="wrap"><div class="panel"><div class="controls"><b>Phone Alerts (Telegram)</b><input id="bot" placeholder="Bot Token" style="min-width:220px"><input id="chat" placeholder="Chat ID"><button onclick="saveAlerts()">SAVE ALERTS</button><button onclick="testAlert()">TEST PHONE</button></div><div class="small">NSE data source. Keep this page open during trading hours for browser-driven scans and alerts.</div></div><div class="panel"><div class="controls"><b>Investment Budget ₹</b><input id="budget" type="number" value="5000"><b>Max Loss/Trade ₹</b><input id="risk" type="number" value="500"><b>Max Trades</b><input id="maxtrades" type="number" value="2"><select id="mode"><option>ALL STOCKS</option><option>BUY ONLY</option><option>SELL ONLY</option><option>WAIT ONLY</option></select><button onclick="scan()">SCAN NSE</button><span id="msg">Starting…</span></div></div><div class="panel"><div class="cards"><div class="card">NSE Stocks<span id="stocks" class="num">0</span></div><div class="card">BUY<span id="buys" class="num buy">0</span></div><div class="card">SELL<span id="sells" class="num sell">0</span></div><div class="card">WAIT<span id="waits" class="num wait">0</span></div><div class="card">Best Score<span id="topscore" class="num">—</span></div><div class="card">Data Source<span id="source" class="num">—</span></div></div></div><div class="panel"><b>🤖 TRADING GUIDE</b><div id="guide">NSE scanner loads live equity data and adds detailed 5-minute confirmation to the most active stocks.</div></div><div class="panel"><h3>NSE Equity Scanner</h3><div style="overflow:auto"><table><thead><tr><th>Stock</th><th>Price</th><th>Change</th><th>RSI</th><th>VWAP</th><th>EMA9</th><th>EMA21</th><th>Volume</th><th>Score</th><th>Signal</th><th>Budget Qty</th><th>Risk Qty</th><th>Plan Qty</th><th>Target</th><th>SL</th></tr></thead><tbody id="rows"></tbody></table></div></div><div class="panel small">Primary market data: NSE. NSE website/API access can be rate-limited or unavailable temporarily. The scanner does not guarantee profit and is not an order-placement system.</div></div><script>let nextAt=0;function tick(){let d=new Date();clock.textContent='🕐 '+d.toLocaleTimeString('en-IN',{hour12:true});date.textContent='📅 '+d.toLocaleDateString('en-IN');if(nextAt)next.textContent='Next scan: '+Math.max(0,Math.ceil((nextAt-Date.now())/1000))+'s'}setInterval(tick,1000);tick();function saveAlerts(){fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({bot_token:bot.value.trim(),chat_id:chat.value.trim(),alerts:true})}).then(r=>r.json()).then(x=>msg.textContent=x.ok?'Alerts saved':'Save failed').catch(()=>msg.textContent='Save failed')}function testAlert(){fetch('/api/test').then(r=>r.json()).then(x=>msg.textContent=x.ok?'Telegram test sent':'Telegram test failed').catch(()=>msg.textContent='Telegram test failed')}function qty(p){let b=+budget.value||0,r=+risk.value||0;return{b:p?Math.max(0,Math.floor(b/p)):0,r:p?Math.max(0,Math.floor(r/(p*.01))):0}}function render(a){let ok=a.filter(x=>!x.error),m=mode.value;let rr=ok.filter(x=>m==='ALL STOCKS'||(m==='BUY ONLY'&&x.signal==='BUY')||(m==='SELL ONLY'&&x.signal==='SELL')||(m==='WAIT ONLY'&&x.signal==='WAIT')).sort((x,y)=>y.score-x.score);stocks.textContent=ok.length;buys.textContent=ok.filter(x=>x.signal==='BUY').length;sells.textContent=ok.filter(x=>x.signal==='SELL').length;waits.textContent=ok.filter(x=>x.signal==='WAIT').length;source.textContent=ok.length?'NSE':'—';topscore.textContent=ok.length?ok[0].score:'—';rows.innerHTML=rr.map(x=>{let q=qty(x.price),p=Math.min(q.b,q.r||q.b),target=x.price?(x.price*1.015).toFixed(2):'—',sl=x.price?(x.price*.99).toFixed(2):'—';return `<tr><td><b>${x.symbol}</b></td><td>${x.price==null?'—':'₹'+x.price}</td><td>${x.change==null?'—':x.change+'%'}</td><td>${x.rsi??'—'}</td><td>${x.vwap??'—'}</td><td>${x.ema9??'—'}</td><td>${x.ema21??'—'}</td><td>${x.volume||0}</td><td>${x.score}</td><td class="${x.signal.toLowerCase()}"><b>${x.signal}</b></td><td>${q.b}</td><td>${q.r}</td><td>${p}</td><td>₹${target}</td><td>₹${sl}</td></tr>`}).join('')}async function scan(){msg.textContent='Scanning NSE…';try{let r=await fetch('/api/scan');let d=await r.json();if(!d.ok){msg.textContent=d.error||'NSE data unavailable';return}render(d.rows);market.textContent='MARKET '+d.market;last.textContent='Last update: '+d.time;nextAt=Date.now()+45000;msg.textContent='Scanned '+d.rows.filter(x=>!x.error).length+'/'+d.rows.length+' NSE equity stocks'}catch(e){msg.textContent='NSE scan failed'}}scan();setInterval(scan,60000);mode.addEventListener('change',()=>{if(window._rows)render(window._rows)})</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def send_json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        if self.path == '/health':
            self.send_json({'ok': True, 'service': 'Intraday AI', 'source': 'NSE'}); return
        if self.path == '/':
            b = HTML.encode(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b); return
        if self.path.startswith('/api/scan'):
            try:
                rows = scan(); self.send_json({'ok': True, 'rows': rows, 'market': market_state(), 'time': now_ist().strftime('%I:%M:%S %p')})
            except Exception as e:
                self.send_json({'ok': False, 'error': 'NSE data unavailable: ' + str(e)[:180]}, 503)
            return
        if self.path == '/api/test':
            self.send_json({'ok': telegram('🟢 Intraday AI Telegram test — NSE data connection app is running')}); return
        self.send_response(404); self.end_headers()

    def do_POST(self):
        if self.path == '/api/settings':
            try:
                n = int(self.headers.get('Content-Length', '0')); data = json.loads(self.rfile.read(n) or '{}'); save_settings(data); self.send_json({'ok': True})
            except Exception:
                self.send_json({'ok': False}, 400)
            return
        self.send_response(404); self.end_headers()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '10000'))
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()
