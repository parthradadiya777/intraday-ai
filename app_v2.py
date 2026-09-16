import json, os, urllib.parse, urllib.request, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo

SYMBOLS=['RELIANCE','TCS','INFY','HDFCBANK','ICICIBANK','SBIN','ITC','LT','AXISBANK','KOTAKBANK','BHARTIARTL','HINDUNILVR','MARUTI','M&M','SUNPHARMA','TATAMOTORS','TATASTEEL','NTPC','POWERGRID','ADANIENT','ADANIPORTS','WIPRO','HCLTECH','TECHM','ONGC','COALINDIA','IOC','BEL','HAL','TRENT','BAJFINANCE','BAJAJFINSV','INDUSINDBK','EICHERMOT','ASIANPAINT','TITAN','ULTRACEMCO','GRASIM','JSWSTEEL','HINDALCO','DRREDDY','CIPLA','APOLLOHOSP','DIVISLAB','SBILIFE','HDFCLIFE','BRITANNIA','NESTLEIND','HEROMOTOCO','TVSMOTOR','DLF','VEDL','JINDALSTEL','PNB','BANKBARODA','CANBK','IDFCFIRSTB','IRCTC','RVNL','IRFC','SAIL','NHPC','JIOFIN','PAYTM','ZOMATO']
DATA_DIR=os.environ.get('DATA_DIR','/tmp/intraday_ai_data'); os.makedirs(DATA_DIR,exist_ok=True)
SETTINGS_FILE=os.path.join(DATA_DIR,'guide_settings.json')
CACHE={'rows':None,'at':0}; LAST_ALERT={}

def now_ist(): return datetime.now(ZoneInfo('Asia/Kolkata'))
def market_state():
    d=now_ist(); m=d.hour*60+d.minute
    if d.weekday()>=5:return 'CLOSED'
    if m<555:return 'PRE-OPEN'
    if m<=930:return 'OPEN'
    return 'CLOSED'

def http_json(url,timeout=10):
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36','Accept':'application/json','Accept-Language':'en-US,en;q=0.9'})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        if r.status!=200: raise RuntimeError('HTTP '+str(r.status))
        return json.load(r)

def ema(a,n):
    if len(a)<n:return None
    k=2/(n+1); e=sum(a[:n])/n
    for x in a[n:]:e=x*k+e*(1-k)
    return e

def rsi(a,n=14):
    if len(a)<n+1:return None
    g=[];l=[]
    for x,y in zip(a[-n-1:-1],a[-n:]):
        d=y-x;g.append(max(d,0));l.append(max(-d,0))
    ag=sum(g)/n; al=sum(l)/n
    return 100 if al==0 else 100-100/(1+ag/al)

def analyze(symbol,closes,volumes=None,highs=None,lows=None,previous_close=None):
    a=[float(x) for x in closes if x is not None]
    if len(a)<25: raise ValueError('need 25 candles')
    p=a[-1]; prev=a[-2]; e9=ema(a,9); e21=ema(a,21); rr=rsi(a)
    change=((p/float(previous_close))-1)*100 if previous_close else ((p/a[-min(len(a),75)]-1)*100)
    mom=(p/prev-1)*100
    bull=bear=0
    if p>e9: bull+=18
    else: bear+=18
    if p>e21: bull+=18
    else: bear+=18
    if e9>e21: bull+=12
    else: bear+=12
    if rr is not None:
        if 52<=rr<=68: bull+=15
        elif 32<=rr<=48: bear+=15
    if mom>0: bull+=12
    elif mom<0: bear+=12
    if change>0: bull+=8
    elif change<0: bear+=8
    score=max(0,min(100,50+bull-bear)); signal='WAIT'
    if bull>=65 and bull-bear>=28: signal='BUY'
    elif bear>=65 and bear-bull>=28: signal='SELL'
    vwap=p
    if volumes and highs and lows:
        pairs=[]
        for h,l,v in zip(highs[-120:],lows[-120:],volumes[-120:]):
            if h is not None and l is not None and v is not None and float(v)>0:pairs.append(((float(h)+float(l)+p)/3,float(v)))
        if pairs:
            den=sum(v for _,v in pairs); vwap=sum(tp*v for tp,v in pairs)/den if den else p
    vol=int(volumes[-1] or 0) if volumes else 0
    return {'symbol':symbol,'price':round(p,2),'change':round(change,2),'momentum':round(mom,3),'volume':vol,'score':round(score,1),'signal':signal,'ema9':round(e9,2),'ema21':round(e21,2),'rsi':round(rr,1) if rr is not None else None,'vwap':round(vwap,2),'source':'Yahoo'}

def chart_one(symbol):
    last='no data'
    for host in ('query1.finance.yahoo.com','query2.finance.yahoo.com'):
        try:
            q=urllib.parse.quote(symbol+'.NS',safe='')
            url=f'https://{host}/v8/finance/chart/{q}?range=5d&interval=5m&events=div%2Csplits&includePrePost=false'
            raw=http_json(url,10); result=(raw.get('chart',{}).get('result') or [None])[0]
            if not result: raise RuntimeError('no chart result')
            ind=result.get('indicators',{}).get('quote',[{}])[0]
            return analyze(symbol,ind.get('close',[]),ind.get('volume',[]),ind.get('high',[]),ind.get('low',[]),result.get('meta',{}).get('previousClose'))
        except Exception as e:last=str(e)
    return {'symbol':symbol,'error':True,'error_detail':last}

def scan(force=False):
    if not force and CACHE['rows'] is not None and time.time()-CACHE['at']<45:return CACHE['rows']
    rows=[]
    with ThreadPoolExecutor(max_workers=4) as ex:
        fs={ex.submit(chart_one,s):s for s in SYMBOLS}
        for f in as_completed(fs):
            try: rows.append(f.result())
            except Exception as e: rows.append({'symbol':fs[f],'error':True,'error_detail':str(e)})
    order={s:i for i,s in enumerate(SYMBOLS)}; rows.sort(key=lambda x:order.get(x['symbol'],999))
    CACHE['rows']=rows; CACHE['at']=time.time()
    return rows

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
        u='https://api.telegram.org/bot'+s['bot_token']+'/sendMessage'; d=urllib.parse.urlencode({'chat_id':s['chat_id'],'text':text}).encode()
        with urllib.request.urlopen(urllib.request.Request(u,data=d,headers={'User-Agent':'IntradayAI'}),timeout=8) as r:return r.status==200
    except Exception:return False

def send_top_alert(rows):
    if market_state()!='OPEN':return
    good=[x for x in rows if not x.get('error') and x.get('signal')=='BUY']
    if not good:return
    x=max(good,key=lambda z:z['score'])
    if x['score']<78:return
    key='buy_'+x['symbol']+'_'+str(x['score'])
    if LAST_ALERT.get(key):return
    if telegram(f"BUY NOW — {x['symbol']}\nPrice ₹{x['price']}\nScore {x['score']}\nTarget ₹{x['price']*1.015:.2f}\nStop Loss ₹{x['price']*.99:.2f}"):LAST_ALERT[key]=1

HTML='''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Intraday AI</title><style>*{box-sizing:border-box}body{margin:0;background:#f3f5f8;color:#172033;font-family:Arial,sans-serif}header{background:#101827;color:#fff;padding:20px 28px}h1{margin:0}.sub{opacity:.7;margin-top:5px}.status,.controls{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.status{margin-top:12px;background:#1d2939;padding:12px;border-radius:10px}.wrap{max-width:1320px;margin:auto;padding:18px}.panel{background:#fff;border-radius:14px;padding:18px;margin-bottom:16px;box-shadow:0 2px 12px #0001}input,button,select{padding:10px 12px;border:1px solid #ccd3dd;border-radius:8px}button{background:#111827;color:#fff;font-weight:800;cursor:pointer}.cards{display:grid;grid-template-columns:repeat(6,1fr);gap:10px}.card{background:#f7f8fa;padding:13px;border-radius:10px}.num{display:block;font-size:21px;font-weight:800;margin-top:5px}.buy{color:#07833a}.sell{color:#c62828}.wait{color:#697386}table{width:100%;border-collapse:collapse}th,td{padding:9px 7px;border-bottom:1px solid #edf0f4;text-align:left;white-space:nowrap}th{font-size:11px;color:#697386}.small{font-size:12px;color:#697386}@media(max-width:900px){.cards{grid-template-columns:repeat(3,1fr)}}@media(max-width:600px){.cards{grid-template-columns:repeat(2,1fr)}}</style></head><body><header><h1>Intraday AI</h1><div class="sub">NSE scanner • trend • RSI • EMA • momentum</div><div class="status"><span id="clock">🕐 --</span><span id="date">📅 --</span><span id="market">MARKET --</span><span id="last">Last update: —</span><span id="next">Next scan: —</span></div></header><div class="wrap"><div class="panel"><div class="controls"><b>Phone Alerts (Telegram)</b><input id="bot" placeholder="Bot Token" style="min-width:220px"><input id="chat" placeholder="Chat ID"><button onclick="saveAlerts()">SAVE ALERTS</button><button onclick="testAlert()">TEST PHONE</button></div><div class="small">Telegram settings are stored on the server. Keep the page open during trading hours for browser-driven alerts.</div></div><div class="panel"><div class="controls"><b>Investment Budget ₹</b><input id="budget" type="number" value="5000"><b>Max Loss/Trade ₹</b><input id="risk" type="number" value="500"><b>Max Trades</b><input id="maxtrades" type="number" value="2"><select id="mode"><option>ALL STOCKS</option><option>BUY ONLY</option><option>SELL ONLY</option><option>WAIT ONLY</option></select><button id="scanBtn" onclick="scan()">SCAN NSE</button><span id="msg">Starting…</span></div></div><div class="panel"><div class="cards"><div class="card">Stocks<span id="stocks" class="num">0</span></div><div class="card">BUY<span id="buys" class="num buy">0</span></div><div class="card">SELL<span id="sells" class="num sell">0</span></div><div class="card">WAIT<span id="waits" class="num wait">0</span></div><div class="card">Best Score<span id="topscore" class="num">—</span></div><div class="card">Data Source<span id="source" class="num">—</span></div></div></div><div class="panel"><b>🤖 TRADING GUIDE</b><div id="guide">Waiting…</div></div><div class="panel"><h3>Stock Scanner</h3><div style="overflow:auto"><table><thead><tr><th>Stock</th><th>Price</th><th>Change</th><th>RSI</th><th>VWAP</th><th>EMA9</th><th>EMA21</th><th>Volume</th><th>Score</th><th>Signal</th><th>Budget Qty</th><th>Risk Qty</th><th>Plan Qty</th><th>Target</th><th>SL</th></tr></thead><tbody id="rows"></tbody></table></div></div><div class="panel small">Free Yahoo chart data is unofficial and may be delayed, rate-limited or temporarily unavailable. Signals do not guarantee profit.</div></div><script>let busy=false,nextAt=0;function tick(){let d=new Date();clock.textContent='🕐 '+d.toLocaleTimeString('en-IN',{hour12:true});date.textContent='📅 '+d.toLocaleDateString('en-IN');let h=d.getHours()*60+d.getMinutes();market.textContent='MARKET '+(d.getDay()==0||d.getDay()==6?'CLOSED':h<555?'PRE-OPEN':h<=930?'OPEN':'CLOSED');if(nextAt)next.textContent='Next scan: '+new Date(nextAt).toLocaleTimeString('en-IN',{hour12:true})}setInterval(tick,1000);tick();function saveAlerts(){fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({bot_token:bot.value.trim(),chat_id:chat.value.trim(),alerts:true})}).then(r=>r.json()).then(x=>msg.textContent=x.ok?'Alerts saved':'Save failed').catch(()=>msg.textContent='Save failed')}function testAlert(){fetch('/api/test').then(r=>r.json()).then(x=>msg.textContent=x.ok?'Telegram test sent':'Telegram test failed').catch(()=>msg.textContent='Telegram test failed')}function qty(p){let b=+budget.value||0,r=+risk.value||0;return{b:Math.max(0,Math.floor(b/p)),r:Math.max(0,Math.floor(r/(p*.01)))}}function render(a){let ok=a.filter(x=>!x.error),m=mode.value;let rr=ok.filter(x=>m==='ALL STOCKS'||(m==='BUY ONLY'&&x.signal==='BUY')||(m==='SELL ONLY'&&x.signal==='SELL')||(m==='WAIT ONLY'&&x.signal==='WAIT')).sort((x,y)=>y.score-x.score);stocks.textContent=ok.length;buys.textContent=ok.filter(x=>x.signal==='BUY').length;sells.textContent=ok.filter(x=>x.signal==='SELL').length;waits.textContent=ok.filter(x=>x.signal==='WAIT').length;source.textContent=ok.length?'Yahoo':'—';topscore.textContent=ok.length?ok[0].score:'—';rows.innerHTML=rr.map(x=>{let q=qty(x.price),p=Math.min(q.b,q.r||q.b);return `<tr><td><b>${x.symbol}</b></td><td>₹${x.price}</td><td>${x.change}%</td><td>${x.rsi??'—'}</td><td>${x.vwap}</td><td>${x.ema9}</td><td>${x.ema21}</td><td>${x.volume||0}</td><td>${x.score}</td><td class="${x.signal.toLowerCase()}"><b>${x.signal}</b></td><td>${q.b}</td><td>${q.r}</td><td>${p}</td><td>₹${(x.price*1.015).toFixed(2)}</td><td>₹${(x.price*.99).toFixed(2)}</td></tr>`}).join('');if(!ok.length){guide.innerHTML='<b>Market data unavailable.</b> Scanner is retrying; do not trade from an empty/failed scan.'}else{let b=ok.filter(x=>x.signal==='BUY').sort((x,y)=>y.score-x.score)[0],s=ok.filter(x=>x.signal==='SELL').sort((x,y)=>y.score-x.score)[0];guide.innerHTML=b?`BUY watch: <b>${b.symbol}</b> — score ${b.score}. Wait for confirmation.`:s?`SELL watch: <b>${s.symbol}</b> — score ${s.score}.`: 'No strong setup; WAIT.'}}async function scan(force=false){if(busy)return;busy=true;scanBtn.disabled=true;msg.textContent='Scanning 65 NSE stocks…';try{let r=await fetch('/api/scan'+(force?'?force=1':''));let x=await r.json();render(x.rows||[]);last.textContent='Last update: '+new Date().toLocaleTimeString('en-IN',{hour12:true});nextAt=Date.now()+60000;msg.textContent=`Scanned ${(x.rows||[]).filter(z=>!z.error).length}/65`}catch(e){msg.textContent='Scanner error — retrying';guide.textContent='Market data unavailable.'}finally{busy=false;scanBtn.disabled=false}}mode.addEventListener('change',()=>{if(window._rows)render(window._rows)});setInterval(()=>scan(false),60000);scan(false);</script></body></html>'''

class Handler(BaseHTTPRequestHandler):
    def send_json(self,obj,code=200):
        b=json.dumps(obj).encode();self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
    def do_GET(self):
        try:
            if self.path=='/' or self.path.startswith('/?'):
                b=HTML.encode();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b);return
            if self.path.startswith('/health'):
                self.send_json({'ok':True,'market':market_state()});return
            if self.path.startswith('/api/scan'):
                rows=scan('force=1' in self.path);send_top_alert(rows);self.send_json({'rows':rows,'market':market_state(),'scanned':sum(1 for x in rows if not x.get('error'))});return
            if self.path.startswith('/api/test'):
                self.send_json({'ok':telegram('Intraday AI test alert — Telegram connection is working.')});return
            if self.path.startswith('/api/settings'):
                s=load_settings();self.send_json({'ok':True,'chat_id':s.get('chat_id',''),'alerts':bool(s.get('alerts'))});return
            self.send_json({'error':'not found'},404)
        except Exception as e:self.send_json({'error':str(e)},500)
    def do_POST(self):
        if self.path.startswith('/api/settings'):
            try:
                n=int(self.headers.get('Content-Length','0'));x=json.loads(self.rfile.read(n) or b'{}');save_settings({'bot_token':str(x.get('bot_token','')).strip(),'chat_id':str(x.get('chat_id','')).strip(),'alerts':bool(x.get('alerts',True))});self.send_json({'ok':True});return
            except Exception as e:self.send_json({'ok':False,'error':str(e)},400);return
        self.send_json({'error':'not found'},404)
    def log_message(self,*args):pass

if __name__=='__main__':
    port=int(os.environ.get('PORT','10000')); ThreadingHTTPServer(('0.0.0.0',port),Handler).serve_forever()
