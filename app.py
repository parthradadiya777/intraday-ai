import json, os, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

DATA_DIR=os.environ.get('DATA_DIR','/tmp/intraday_ai_data')
os.makedirs(DATA_DIR,exist_ok=True)
SETTINGS_FILE=os.path.join(DATA_DIR,'guide_settings.json')
LAST_ALERT={}
SYMBOLS=['RELIANCE','TCS','INFY','HDFCBANK','ICICIBANK','SBIN','ITC','LT','AXISBANK','KOTAKBANK','BHARTIARTL','HINDUNILVR','MARUTI','M&M','SUNPHARMA','TATAMOTORS','TATASTEEL','NTPC','POWERGRID','ADANIENT','ADANIPORTS','WIPRO','HCLTECH','TECHM','ONGC','COALINDIA','IOC','BEL','HAL','TRENT','BAJFINANCE','BAJAJFINSV','INDUSINDBK','EICHERMOT','ASIANPAINT','TITAN','ULTRACEMCO','GRASIM','JSWSTEEL','HINDALCO','DRREDDY','CIPLA','APOLLOHOSP','DIVISLAB','SBILIFE','HDFCLIFE','BRITANNIA','NESTLEIND','HEROMOTOCO','TVSMOTOR','DLF','VEDL','JINDALSTEL','PNB','BANKBARODA','CANBK','IDFCFIRSTB','IRCTC','RVNL','IRFC','SAIL','NHPC','JIOFIN','PAYTM','ZOMATO']

def now_ist():
    return datetime.now(ZoneInfo('Asia/Kolkata')) if ZoneInfo else datetime.now()

def market_state():
    d=now_ist(); m=d.hour*60+d.minute
    if d.weekday()>=5:return 'CLOSED'
    if m<555:return 'PRE-OPEN'
    if m<=930:return 'OPEN'
    return 'CLOSED'

def load_settings():
    try:
        with open(SETTINGS_FILE,encoding='utf-8') as f:return json.load(f)
    except Exception:return {'bot_token':'','chat_id':'','alerts':True}

def save_settings(x):
    with open(SETTINGS_FILE,'w',encoding='utf-8') as f:json.dump(x,f)

def telegram(text):
    s=load_settings()
    if not s.get('alerts') or not s.get('bot_token') or not s.get('chat_id'):return False
    try:
        u='https://api.telegram.org/bot'+s['bot_token']+'/sendMessage'
        d=urllib.parse.urlencode({'chat_id':s['chat_id'],'text':text}).encode()
        r=urllib.request.urlopen(urllib.request.Request(u,data=d,headers={'User-Agent':'IntradayAI'}),timeout=8)
        return r.status==200
    except Exception:return False

def http_json(url,timeout=15):
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36','Accept':'application/json','Accept-Language':'en-US,en;q=0.9'})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        if r.status!=200:raise RuntimeError('HTTP '+str(r.status))
        return json.load(r)

def ema(a,n):
    if len(a)<n:return None
    k=2/(n+1);e=sum(a[:n])/n
    for x in a[n:]:e=x*k+e*(1-k)
    return e

def rsi(a,n=14):
    if len(a)<n+1:return None
    g=[];l=[]
    for x,y in zip(a[-n-1:-1],a[-n:]):
        d=y-x;g.append(max(d,0));l.append(max(-d,0))
    ag=sum(g)/n;al=sum(l)/n
    return 100 if al==0 else 100-100/(1+ag/al)

def analyze(symbol,closes,volume=0):
    a=[float(x) for x in closes if x is not None]
    if len(a)<25:raise ValueError('insufficient candles')
    p=a[-1];prev=a[-2];e9=ema(a,9);e21=ema(a,21);rr=rsi(a)
    change=(p/a[0]-1)*100;mom=(p/prev-1)*100
    bull=bear=0
    if p>e9:bull+=18
    else:bear+=18
    if p>e21:bull+=18
    else:bear+=18
    if e9>e21:bull+=12
    else:bear+=12
    if rr is not None:
        if 52<=rr<=68:bull+=15
        if 32<=rr<=48:bear+=15
    if mom>0:bull+=12
    elif mom<0:bear+=12
    if change>0:bull+=8
    elif change<0:bear+=8
    score=max(0,min(100,50+bull-bear))
    signal='WAIT'
    if bull>=65 and bull-bear>=28:signal='BUY'
    elif bear>=65 and bear-bull>=28:signal='SELL'
    return {'symbol':symbol,'price':round(p,2),'change':round(change,2),'momentum':round(mom,3),'volume':int(volume or 0),'score':round(score,1),'signal':signal,'ema9':round(e9,2),'ema21':round(e21,2),'rsi':round(rr,1) if rr is not None else None,'vwap':round(p,2),'source':'Yahoo'}

def spark_chunk(symbols,host):
    syms=','.join(urllib.parse.quote(s+'.NS',safe='') for s in symbols)
    url=f'https://{host}/v7/finance/spark?symbols={syms}&range=1d&interval=5m&indicators=close&includeTimestamps=false&includePrePost=false&corsDomain=finance.yahoo.com&.tsrc=finance'
    raw=http_json(url,15)
    out={}
    for item in raw.get('spark',{}).get('result',[]) or []:
        sym=item.get('symbol','').replace('.NS','')
        resp=item.get('response',[]) or []
        if resp:
            q=(resp[0].get('indicators',{}).get('quote',[{}]) or [{}])[0]
            out[sym]=q.get('close',[])
    return out

def chart_one(symbol):
    last=None
    for host in ('query1.finance.yahoo.com','query2.finance.yahoo.com'):
        try:
            url=f'https://{host}/v8/finance/chart/{urllib.parse.quote(symbol+".NS",safe="")}?range=1d&interval=5m&events=div%2Csplits&includePrePost=false'
            raw=http_json(url,12); r=(raw.get('chart',{}).get('result') or [None])[0]
            if r:
                q=(r.get('indicators',{}).get('quote',[{}]) or [{}])[0]
                return analyze(symbol,q.get('close',[]),q.get('volume',[0])[-1] if q.get('volume') else 0)
        except Exception as e:last=e
    return {'symbol':symbol,'error':True,'error_detail':str(last)[:140] if last else 'no data'}

def scan():
    data={s:{'symbol':s,'error':True,'error_detail':'not scanned'} for s in SYMBOLS}
    # Keep Yahoo requests small; a very large Spark URL can fail or be rate-limited.
    chunks=[SYMBOLS[i:i+10] for i in range(0,len(SYMBOLS),10)]
    for host in ('query1.finance.yahoo.com','query2.finance.yahoo.com'):
        pending=[c for c in chunks if any(data[s].get('error') for s in c)]
        if not pending:break
        with ThreadPoolExecutor(max_workers=5) as ex:
            fs={ex.submit(spark_chunk,c,host):c for c in pending}
            for f in as_completed(fs):
                c=fs[f]
                try:
                    got=f.result()
                    for s in c:
                        if s in got:
                            try:data[s]=analyze(s,got[s])
                            except Exception:pass
                except Exception:pass
    missing=[s for s in SYMBOLS if data[s].get('error')]
    # Per-symbol chart fallback for anything missing from Spark.
    if missing:
        with ThreadPoolExecutor(max_workers=8) as ex:
            fs={ex.submit(chart_one,s):s for s in missing}
            for f in as_completed(fs):
                x=f.result();data[x['symbol']]=x
    rows=[data[s] for s in SYMBOLS]
    ok=[x for x in rows if not x.get('error')]
    if market_state()=='OPEN' and ok:
        buys=sorted([x for x in ok if x['signal']=='BUY'],key=lambda x:x['score'],reverse=True)
        if buys and buys[0]['score']>=78:
            x=buys[0]; key='buy_'+x['symbol']+'_'+str(x['score'])
            if not LAST_ALERT.get(key) and telegram(f"🟢 BUY NOW — {x['symbol']}\nPrice ₹{x['price']}\nScore {x['score']}\nTarget ₹{x['price']*1.015:.2f}\nStop Loss ₹{x['price']*.99:.2f}"):LAST_ALERT[key]=1
    return rows

HTML='''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Intraday AI</title><style>*{box-sizing:border-box}body{margin:0;background:#f3f5f8;color:#172033;font-family:Arial,sans-serif}header{background:#101827;color:#fff;padding:20px 28px}h1{margin:0}.sub{opacity:.7;margin-top:5px}.status,.controls{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.status{margin-top:12px;background:#1d2939;padding:12px;border-radius:10px}.wrap{max-width:1320px;margin:auto;padding:18px}.panel{background:#fff;border-radius:14px;padding:18px;margin-bottom:16px;box-shadow:0 2px 12px #0001}input,button,select{padding:10px 12px;border:1px solid #ccd3dd;border-radius:8px}button{background:#111827;color:#fff;font-weight:800;cursor:pointer}.cards{display:grid;grid-template-columns:repeat(6,1fr);gap:10px}.card{background:#f7f8fa;padding:13px;border-radius:10px}.num{display:block;font-size:21px;font-weight:800;margin-top:5px}.buy{color:#07833a}.sell{color:#c62828}.wait{color:#697386}table{width:100%;border-collapse:collapse}th,td{padding:9px 7px;border-bottom:1px solid #edf0f4;text-align:left;white-space:nowrap}th{font-size:11px;color:#697386}.small{font-size:12px;color:#697386}@media(max-width:900px){.cards{grid-template-columns:repeat(3,1fr)}}@media(max-width:600px){.cards{grid-template-columns:repeat(2,1fr)}}</style></head><body><header><h1>Intraday AI</h1><div class="sub">NSE scanner • trend • RSI • EMA • momentum</div><div class="status"><span id="clock">🕐 --</span><span id="date">📅 --</span><span id="market">MARKET --</span><span id="last">Last update: —</span><span id="next">Next scan: —</span></div></header><div class="wrap"><div class="panel"><div class="controls"><b>Phone Alerts (Telegram)</b><input id="bot" placeholder="Bot Token" style="min-width:220px"><input id="chat" placeholder="Chat ID"><button onclick="saveAlerts()">SAVE ALERTS</button><button onclick="testAlert()">TEST PHONE</button></div><div class="small">Keep this page open during trading hours for browser-driven scans and alerts.</div></div><div class="panel"><div class="controls"><b>Investment Budget ₹</b><input id="budget" type="number" value="5000"><b>Max Loss/Trade ₹</b><input id="risk" type="number" value="500"><b>Max Trades</b><input id="maxtrades" type="number" value="2"><select id="mode"><option>ALL STOCKS</option><option>BUY ONLY</option><option>SELL ONLY</option><option>WAIT ONLY</option></select><button onclick="scan()">SCAN NSE</button><span id="msg">Starting…</span></div></div><div class="panel"><div class="cards"><div class="card">Stocks<span id="stocks" class="num">0</span></div><div class="card">BUY<span id="buys" class="num buy">0</span></div><div class="card">SELL<span id="sells" class="num sell">0</span></div><div class="card">WAIT<span id="waits" class="num wait">0</span></div><div class="card">Best Score<span id="topscore" class="num">—</span></div><div class="card">Data Source<span id="source" class="num">—</span></div></div></div><div class="panel"><b>🤖 TRADING GUIDE</b><div id="guide">Waiting…</div></div><div class="panel"><h3>Stock Scanner</h3><div style="overflow:auto"><table><thead><tr><th>Stock</th><th>Price</th><th>Change</th><th>RSI</th><th>VWAP</th><th>EMA9</th><th>EMA21</th><th>Volume</th><th>Score</th><th>Signal</th><th>Budget Qty</th><th>Risk Qty</th><th>Plan Qty</th><th>Target</th><th>SL</th></tr></thead><tbody id="rows"></tbody></table></div></div><div class="panel small">Data uses free Yahoo Finance endpoints. It may be delayed, rate-limited or temporarily unavailable and is not exchange-grade real-time data. Signals do not guarantee profit.</div></div><script>let nextAt=0;function tick(){let d=new Date();clock.textContent='🕐 '+d.toLocaleTimeString('en-IN',{hour12:true});date.textContent='📅 '+d.toLocaleDateString('en-IN')}setInterval(tick,1000);tick();function saveAlerts(){fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({bot_token:bot.value.trim(),chat_id:chat.value.trim(),alerts:true})}).then(r=>r.json()).then(x=>msg.textContent=x.ok?'Alerts saved':'Save failed').catch(()=>msg.textContent='Save failed')}function testAlert(){fetch('/api/test').then(r=>r.json()).then(x=>msg.textContent=x.ok?'Telegram test sent':'Telegram test failed').catch(()=>msg.textContent='Telegram test failed')}function qty(p){let b=+budget.value||0,r=+risk.value||0;return{b:Math.max(0,Math.floor(b/p)),r:Math.max(0,Math.floor(r/(p*.01)))}}function render(a){let ok=a.filter(x=>!x.error),m=mode.value;let rr=a.filter(x=>!x.error).filter(x=>m==='ALL STOCKS'||(m==='BUY ONLY'&&x.signal==='BUY')||(m==='SELL ONLY'&&x.signal==='SELL')||(m==='WAIT ONLY'&&x.signal==='WAIT')).sort((x,y)=>y.score-x.score);stocks.textContent=ok.length;buys.textContent=ok.filter(x=>x.signal==='BUY').length;sells.textContent=ok.filter(x=>x.signal==='SELL').length;waits.textContent=ok.filter(x=>x.signal==='WAIT').length;source.textContent=ok.length?'Yahoo':'—';topscore.textContent=ok.length?ok[0].score:'—';rows.innerHTML=rr.map(x=>{let q=qty(x.price),p=Math.min(q.b,q.r||q.b);return `<tr><td><b>${x.symbol}</b></td><td>₹${x.price}</td><td>${x.change}%</td><td>${x.rsi??'—'}</td><td>${x.vwap}</td><td>${x.ema9}</td><td>${x.ema21}</td><td>${x.volume||0}</td><td>${x.score}</td><td class="${x.signal.toLowerCase()}"><b>${x.signal}</b></td><td>${q.b}</td><td>${q.r}</td><td>${p}</td><td>₹${(x.price*1.015).toFixed(2)}</td><td>₹${(x.price*.99).toFixed(2)}</td></tr>`}).join('');if(ok[0])guide.innerHTML=ok[0].signal==='BUY'?`🟢 <b>BUY WATCH</b> — ${ok[0].symbol} ₹${ok[0].price}`:ok[0].signal==='SELL'?`🔴 <b>SELL SETUP</b> — ${ok[0].symbol} ₹${ok[0].price}`:`⚪ <b>WAIT</b> — strongest current stock: ${ok[0].symbol} ₹${ok[0].price}`}function scan(){msg.textContent='Scanning NSE…';fetch('/api/scan').then(r=>r.json()).then(x=>{render(x.data);last.textContent='Last update: '+new Date().toLocaleTimeString('en-IN');nextAt=Date.now()+15000;msg.textContent=`Scanned ${x.meta.ok}/${x.meta.total}`;market.textContent='MARKET '+x.meta.market;next.textContent='Next scan: 15s'}).catch(()=>msg.textContent='Scan connection error')}setInterval(()=>{if(nextAt&&Date.now()>=nextAt){nextAt=0;scan()}},1000);scan()</script></body></html>'''

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def send_json(self,x,code=200):
        b=json.dumps(x,ensure_ascii=False).encode();self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
    def do_GET(self):
        if self.path in ('/','/index.html'):
            b=HTML.encode();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b);return
        if self.path.startswith('/api/scan'):
            a=scan();self.send_json({'data':a,'meta':{'ok':sum(not x.get('error') for x in a),'total':len(a),'market':market_state()}});return
        if self.path.startswith('/api/test'):
            self.send_json({'ok':telegram('✅ Intraday AI Telegram test successful.')});return
        if self.path.startswith('/api/settings'):
            self.send_json(load_settings());return
        self.send_error(404)
    def do_POST(self):
        if self.path.startswith('/api/settings'):
            n=int(self.headers.get('Content-Length','0') or 0);raw=self.rfile.read(n)
            try:x=json.loads(raw.decode() or '{}')
            except Exception:x={}
            save_settings({'bot_token':str(x.get('bot_token','')).strip(),'chat_id':str(x.get('chat_id','')).strip(),'alerts':bool(x.get('alerts',True))});self.send_json({'ok':True});return
        self.send_error(404)

def main():
    port=int(os.environ.get('PORT','10000'))
    ThreadingHTTPServer(('0.0.0.0',port),Handler).serve_forever()
if __name__=='__main__':main()
