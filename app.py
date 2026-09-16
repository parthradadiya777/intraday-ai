import json, urllib.request, urllib.parse, webbrowser, threading, math, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import os

DATA_DIR = os.environ.get("DATA_DIR", "/tmp/intraday_ai_data")
os.makedirs(DATA_DIR, exist_ok=True)
SETTINGS_FILE = os.path.join(DATA_DIR, "guide_settings.json")
LAST_ALERT = {}
LAST_GUIDE = {"action":"WAIT", "symbol":None, "score":0}

API_BASE = os.environ.get("MARKET_API_BASE", "https://65.0.104.9")


def load_settings():
    try:
        with open(SETTINGS_FILE,"r",encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"bot_token":"","chat_id":"","alerts":True}


def save_settings(s):
    with open(SETTINGS_FILE,"w",encoding="utf-8") as f:
        json.dump(s,f)


def telegram_send(text):
    s=load_settings()
    if not s.get("alerts") or not s.get("bot_token") or not s.get("chat_id"):
        return False
    try:
        url="https://api.telegram.org/bot"+s["bot_token"]+"/sendMessage"
        data=urllib.parse.urlencode({"chat_id":s["chat_id"],"text":text}).encode()
        req=urllib.request.Request(url,data=data,headers={"User-Agent":"IntradayAI"})
        with urllib.request.urlopen(req,timeout=8) as r:
            return r.status==200
    except Exception:
        return False


def alert_once(key,text):
    if LAST_ALERT.get(key):
        return
    if telegram_send(text):
        LAST_ALERT[key]=True


def market_state():
    d=datetime.now()
    mins=d.hour*60+d.minute
    wd=d.weekday()
    if wd>=5: return "CLOSED"
    if mins < 555: return "PRE-OPEN"
    if mins <= 930: return "OPEN"
    return "CLOSED"

SYMBOLS = ["RELIANCE","TCS","INFY","HDFCBANK","ICICIBANK","SBIN","ITC","LT","AXISBANK","KOTAKBANK",
"BHARTIARTL","HINDUNILVR","MARUTI","M&M","SUNPHARMA","TATAMOTORS","TATASTEEL","NTPC","POWERGRID",
"ADANIENT","ADANIPORTS","WIPRO","HCLTECH","TECHM","ONGC","COALINDIA","IOC","BEL","HAL","TRENT",
"BAJFINANCE","BAJAJFINSV","INDUSINDBK","EICHERMOT","ASIANPAINT","TITAN","ULTRACEMCO","GRASIM",
"JSWSTEEL","HINDALCO","DRREDDY","CIPLA","APOLLOHOSP","DIVISLAB","SBILIFE","HDFCLIFE","BRITANNIA",
"NESTLEIND","HEROMOTOCO","TVSMOTOR","DLF","VEDL","JINDALSTEL","PNB","BANKBARODA","CANBK","IDFCFIRSTB",
"IRCTC","RVNL","IRFC","SAIL","NHPC","JIOFIN","PAYTM","ZOMATO"]


def http_json(url, timeout=12):
    req=urllib.request.Request(url,headers={"User-Agent":"IntradayAI/1.0","Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}")
        return json.load(r)


def yahoo(symbol):
    url=f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}.NS?range=1d&interval=1m"
    return http_json(url, timeout=10)["chart"]["result"][0]


def free_api_quote(symbol):
    # Fallback free Indian market API. It returns a compact live snapshot.
    base=API_BASE.rstrip("/")
    url=base+"/stock?symbol="+urllib.parse.quote(symbol+".NS")+"&res=num"
    return http_json(url, timeout=8)


def normalize_num(v):
    if v is None: return None
    if isinstance(v,(int,float)): return float(v)
    if isinstance(v,dict):
        x=v.get("value")
        if isinstance(x,(int,float)): return float(x)
    try: return float(str(v).replace(",",""))
    except Exception: return None


def ema(vals, n):
    if len(vals) < n: return None
    k=2/(n+1)
    e=sum(vals[:n])/n
    for x in vals[n:]:
        e=x*k+e*(1-k)
    return e


def rsi(vals, n=14):
    if len(vals)<n+1: return None
    gains=[]; losses=[]
    for a,b in zip(vals[-n-1:-1], vals[-n:]):
        d=b-a
        gains.append(max(d,0)); losses.append(max(-d,0))
    ag=sum(gains)/n; al=sum(losses)/n
    if al==0: return 100
    return 100-(100/(1+ag/al))


def analyze_snapshot(symbol, p, ch, volume=0, history=None):
    # Snapshot fallback: when candle history is unavailable, keep the signal conservative.
    p=float(p)
    change=float(ch or 0)
    score=50
    bear=0
    if change > 0: score += min(25, change*8)
    if change < 0: bear += min(25, abs(change)*8)
    if change >= 0.8: score += 10
    if change <= -0.8: bear += 10
    final=max(0,min(100,score-bear+50-50))
    signal="WAIT"
    if score>=72 and score-bear>=30: signal="BUY"
    elif bear>=72 and bear-score>=30: signal="SELL"
    return {"symbol":symbol,"price":round(p,2),"change":round(change,2),"momentum":round(change,3),
            "volume":int(volume or 0),"score":round(final,1),"signal":signal,
            "ema9":None,"ema21":None,"rsi":None,"vwap":round(p,2),"source":"Free API"}


def one(symbol):
    # Try Yahoo candles first for technical indicators.
    try:
        r=yahoo(symbol)
        q=r["indicators"]["quote"][0]
        close=[x for x in q.get("close",[]) if x is not None]
        high=[x for x in q.get("high",[]) if x is not None]
        low=[x for x in q.get("low",[]) if x is not None]
        vol=[x for x in q.get("volume",[]) if x is not None]
        if len(close)>=25:
            p=close[-1]; prev=close[-2]
            e9=ema(close,9); e21=ema(close,21); rr=rsi(close,14)
            avgvol=sum(vol[-20:])/len(vol[-20:]) if vol[-20:] else 0
            v=vol[-1] if vol else 0
            ch=(p/close[0]-1)*100
            mom=(p/prev-1)*100
            recent_high=max(high[-20:]); recent_low=min(low[-20:])
            pv=sum(((h+l+c)/3)*vv for h,l,c,vv in zip(high,low,close,vol))
            tv=sum(vol)
            vwap=pv/tv if tv else p
            score=0; bear=0
            if e9 and p>e9: score+=15
            if e21 and p>e21: score+=15
            if e9 and e21 and e9>e21: score+=10
            if rr is not None and 52<=rr<=68: score+=12
            if rr is not None and rr>70: score-=8
            if p>vwap: score+=12
            if mom>0: score+=10
            if ch>0: score+=8
            if avgvol and v>=1.2*avgvol: score+=10
            if p>=recent_high*0.998: score+=8
            if e9 and p<e9: bear+=15
            if e21 and p<e21: bear+=15
            if e9 and e21 and e9<e21: bear+=10
            if rr is not None and 32<=rr<=48: bear+=12
            if p<vwap: bear+=12
            if mom<0: bear+=10
            if ch<0: bear+=8
            if avgvol and v>=1.2*avgvol: bear+=10
            if p<=recent_low*1.002: bear+=8
            signal="WAIT"
            final=max(0,min(100,50+score-bear))
            if score>=70 and score-bear>=28: signal="BUY"
            elif bear>=70 and bear-score>=28: signal="SELL"
            return {"symbol":symbol,"price":round(p,2),"change":round(ch,2),"momentum":round(mom,3),
                    "volume":int(v),"score":round(final,1),"signal":signal,
                    "ema9":round(e9,2) if e9 else None,"ema21":round(e21,2) if e21 else None,
                    "rsi":round(rr,1) if rr is not None else None,"vwap":round(vwap,2),"source":"Yahoo"}
        raise ValueError("Yahoo returned insufficient candles")
    except Exception as yahoo_err:
        # Fallback to a single no-key market snapshot so the whole scan does not collapse to 0.
        try:
            x=free_api_quote(symbol)
            p=(normalize_num(x.get("price")) or normalize_num(x.get("last_price")) or
               normalize_num(x.get("lastPrice")) or normalize_num(x.get("ltp")))
            ch=(normalize_num(x.get("change_percent")) or normalize_num(x.get("changePct")) or
                normalize_num(x.get("pChange")) or normalize_num(x.get("change")))
            vol=(normalize_num(x.get("volume")) or normalize_num(x.get("volumeTraded")) or 0)
            if p is not None:
                return analyze_snapshot(symbol,p,ch or 0,vol)
        except Exception:
            pass
        return {"symbol":symbol,"error":True,"error_detail":str(yahoo_err)[:120]}


def scan():
    global LAST_GUIDE
    state=market_state()
    if state=="OPEN":
        alert_once("open_"+datetime.now().strftime("%Y%m%d"),
                   "🟢 MARKET OPEN\nIntraday AI is scanning NSE.\nI will alert you when a confirmed BUY/SELL setup appears.")
    if state=="CLOSED":
        alert_once("close_"+datetime.now().strftime("%Y%m%d"),
                   "🔴 MARKET CLOSED\nIntraday AI has stopped live trading signals for today.")
    with ThreadPoolExecutor(max_workers=12) as ex:
        futures=[ex.submit(one,s) for s in SYMBOLS]
        data=[f.result() for f in as_completed(futures)]
    ok=[x for x in data if not x.get("error")]
    if state=="OPEN" and ok:
        buys=sorted([x for x in ok if x["signal"]=="BUY"],key=lambda x:x["score"],reverse=True)
        sells=sorted([x for x in ok if x["signal"]=="SELL"],key=lambda x:x["score"])
        if buys:
            top=buys[0]
            if top["score"]>=78:
                LAST_GUIDE={"action":"BUY","symbol":top["symbol"],"score":top["score"]}
                alert_once("buy_"+top["symbol"]+"_"+str(top["score"]),
                    f"🟢 BUY NOW — {top['symbol']}\nPrice ₹{top['price']}\nScore {top['score']}\nEntry ₹{top['price']:.2f}\nTarget ₹{top['price']*1.015:.2f}\nStop Loss ₹{top['price']*.99:.2f}\nConfirm before placing any order.")
        elif sells:
            top=sells[0]
            if top["score"]<=22:
                LAST_GUIDE={"action":"SELL","symbol":top["symbol"],"score":top["score"]}
                alert_once("sell_"+top["symbol"]+"_"+str(top["score"]),
                    f"🔴 SELL SETUP — {top['symbol']}\nPrice ₹{top['price']}\nScore {top['score']}\nThis is a short/bearish setup, not an EXIT of an existing long position.")
        else:
            LAST_GUIDE={"action":"WAIT","symbol":None,"score":0}
    else:
        LAST_GUIDE={"action":"WAIT","symbol":None,"score":0}
    return data

HTML = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Intraday AI</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f3f5f8;color:#172033;font-family:Arial,sans-serif}
header{background:#101827;color:#fff;padding:20px 28px}h1{margin:0;font-size:25px}.sub{opacity:.72;margin-top:5px}
.status{margin-top:12px;background:#1d2939;border-radius:12px;padding:12px;display:flex;gap:18px;flex-wrap:wrap}.badge{font-weight:700}
.wrap{max-width:1320px;margin:auto;padding:18px}.panel{background:#fff;border-radius:14px;padding:18px;margin-bottom:16px;box-shadow:0 2px 12px #0001}
.controls{display:flex;gap:10px;align-items:center;flex-wrap:wrap}input,button,select{padding:10px 12px;border:1px solid #ccd3dd;border-radius:8px}button{background:#111827;color:#fff;font-weight:800;cursor:pointer}
.cards{display:grid;grid-template-columns:repeat(6,1fr);gap:10px}.card{background:#f7f8fa;border-radius:10px;padding:13px}.num{display:block;font-size:21px;font-weight:800;margin-top:5px}
.buy{color:#07833a;font-weight:800}.sell{color:#c62828;font-weight:800}.wait{color:#697386;font-weight:700}
.best{border-left:5px solid #07833a;background:#ecfff4}.none{border-left:5px solid #697386;background:#f5f6f8}.warn{background:#fff8e8}
table{width:100%;border-collapse:collapse}th,td{padding:9px 7px;border-bottom:1px solid #edf0f4;text-align:left;white-space:nowrap}th{font-size:11px;color:#697386}
tr.click{cursor:pointer}tr.click:hover{background:#f5f8fb}#chart{width:100%;height:300px;display:block;background:#101827;border-radius:12px}
.small{font-size:12px;color:#697386}.strong{font-weight:800}
@media(max-width:900px){.cards{grid-template-columns:repeat(3,1fr)}}@media(max-width:600px){.cards{grid-template-columns:repeat(2,1fr)}}
</style></head><body>
<header><h1>Intraday AI</h1><div class="sub">Trend + VWAP + RSI + EMA + momentum + volume + market confirmation</div>
<div class="status"><span id="clock">🕐 --</span><span id="date">📅 --</span><span id="market">MARKET --</span><span id="last">Last data update: —</span><span id="next">Next scan: —</span></div></header>
<div class="wrap">
<div class="panel"><div class="controls"><b>Phone Alerts (Telegram)</b><input id="bot" placeholder="Bot Token" style="min-width:220px"><input id="chat" placeholder="Chat ID"><button onclick="saveAlerts()">SAVE ALERTS</button><button onclick="testAlert()">TEST PHONE</button></div><div class="small" style="margin-top:8px">The app itself will guide you. Keep the app open during trading hours for browser-driven scans and alerts.</div></div>
<div class="panel"><div class="controls"><b>Investment Budget ₹</b><input id="budget" type="number" value="5000"><b>Max Loss/Trade ₹</b><input id="risk" type="number" value="500"><b>Max Trades</b><input id="maxtrades" type="number" value="2"><select id="mode"><option>ALL STOCKS</option><option>BUY ONLY</option><option>SELL ONLY</option><option>WAIT ONLY</option></select><button onclick="scan()">SCAN NSE</button><span id="msg">Starting…</span></div></div>
<div class="panel"><div class="cards"><div class="card">Stocks<span id="stocks" class="num">0</span></div><div class="card">BUY<span id="buys" class="num buy">0</span></div><div class="card">SELL<span id="sells" class="num sell">0</span></div><div class="card">WAIT<span id="waits" class="num wait">0</span></div><div class="card">Best Score<span id="topscore" class="num">—</span></div><div class="card">Data Source<span id="source" class="num">—</span></div></div></div>
<div id="best" class="panel none"><b>🤖 TRADING GUIDE</b><div id="besttext">Waiting…</div><div id="reason" class="small" style="margin-top:7px"></div></div>
<div class="panel"><h3>Stock Scanner</h3><div style="overflow:auto"><table><thead><tr><th>Stock</th><th>Price</th><th>Change</th><th>RSI</th><th>VWAP</th><th>EMA9</th><th>EMA21</th><th>Volume</th><th>Score</th><th>Signal</th><th>Budget Qty</th><th>Risk Qty</th><th>Plan Qty</th><th>Target</th><th>SL</th></tr></thead><tbody id="rows"></tbody></table></div></div>
<div class="panel"><h3 id="chartTitle">Stock Chart — click a stock</h3><canvas id="chart"></canvas><div id="chartInfo" class="small"></div></div>
<div class="panel"><h3>Signal History</h3><table><thead><tr><th>Time</th><th>Stock</th><th>Signal</th><th>Score</th><th>Price</th></tr></thead><tbody id="history"></tbody></table></div>
<div class="panel warn"><b>Data note:</b> The scanner now has a fallback free Indian market API, but free data can still be delayed, cached, rate-limited or temporarily unavailable. Signals are informational and do not guarantee profit.</div>
</div>
<script>
let nextAt=0,signalHistory=[];
function tick(){let d=new Date();clock.textContent='🕐 '+d.toLocaleTimeString('en-IN',{hour12:true});date.textContent='📅 '+d.toLocaleDateString('en-IN');}
setInterval(tick,1000);tick();
function saveAlerts(){fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({bot_token:bot.value.trim(),chat_id:chat.value.trim(),alerts:true})}).then(r=>r.json()).then(x=>msg.textContent=x.ok?'Alerts saved':'Save failed').catch(()=>msg.textContent='Save failed')}
function testAlert(){fetch('/api/test').then(r=>r.json()).then(x=>msg.textContent=x.ok?'Telegram test sent':'Telegram test failed').catch(()=>msg.textContent='Telegram test failed')}
function qty(p){let b=+budget.value||0;let riskv=+risk.value||0;let riskPer=Math.max(p*.01,.01);return {b:Math.max(0,Math.floor(b/p)),r:Math.max(0,Math.floor(riskv/riskPer))}}
function render(data){let ok=data.filter(x=>!x.error);let modev=mode.value;let rows=data.filter(x=>!x.error).filter(x=>modev==='ALL STOCKS'||(modev==='BUY ONLY'&&x.signal==='BUY')||(modev==='SELL ONLY'&&x.signal==='SELL')||(modev==='WAIT ONLY'&&x.signal==='WAIT')).sort((a,b)=>b.score-a.score);stocks.textContent=ok.length;buys.textContent=ok.filter(x=>x.signal==='BUY').length;sells.textContent=ok.filter(x=>x.signal==='SELL').length;waits.textContent=ok.filter(x=>x.signal==='WAIT').length;source.textContent=ok.length?([...(new Set(ok.map(x=>x.source||'Market')))].join('/')):'—';topscore.textContent=ok.length?ok[0].score:'—';
rows.innerHTML=rows.map(x=>{let q=qty(x.price);let plan=Math.min(q.b,q.r||q.b);let target=x.price*1.015,sl=x.price*.99;return `<tr class="click" onclick='showChart(${JSON.stringify(x)})'><td><b>${x.symbol}</b></td><td>₹${x.price}</td><td>${x.change}%</td><td>${x.rsi??'—'}</td><td>${x.vwap??'—'}</td><td>${x.ema9??'—'}</td><td>${x.ema21??'—'}</td><td>${x.volume||0}</td><td>${x.score}</td><td class="${x.signal.toLowerCase()}">${x.signal}</td><td>${q.b}</td><td>${q.r}</td><td>${plan}</td><td>₹${target.toFixed(2)}</td><td>₹${sl.toFixed(2)}</td></tr>`}).join('');
let top=ok[0];if(top){best.className='panel '+(top.signal==='BUY'?'best':'none');besttext.innerHTML=top.signal==='BUY'?`🟢 <b>BUY WATCH/BUY</b> — ${top.symbol} at ₹${top.price}`:top.signal==='SELL'?`🔴 <b>SELL SETUP</b> — ${top.symbol} at ₹${top.price}`:`⚪ <b>WAIT</b> — strongest current stock: ${top.symbol} at ₹${top.price}`;reason.textContent=`Score ${top.score} • ${top.source||'Market data'}`}}
function showChart(x){chartTitle.textContent='Stock Chart — '+x.symbol;chartInfo.textContent=`Price ₹${x.price} • Change ${x.change}% • Source ${x.source||'Market'}`;let c=chart,ctx=c.getContext('2d');c.width=c.clientWidth;c.height=c.clientHeight;ctx.clearRect(0,0,c.width,c.height);ctx.fillStyle='#fff';ctx.font='20px Arial';ctx.fillText(`${x.symbol}  ₹${x.price}`,20,40);ctx.font='14px Arial';ctx.fillText(`Change ${x.change}%  |  Signal ${x.signal}  |  Score ${x.score}`,20,70)}
function scan(){msg.textContent='Scanning NSE…';fetch('/api/scan').then(r=>r.json()).then(x=>{render(x.data);last.textContent='Last data update: '+new Date().toLocaleTimeString('en-IN');nextAt=Date.now()+15000;msg.textContent=x.meta?`Scanned ${x.meta.ok}/${x.meta.total}`:`Scanned ${x.data.length}`;next.textContent='Next scan: 15s';}).catch(e=>{msg.textContent='Scan connection error';});}
setInterval(()=>{if(nextAt){let s=Math.max(0,Math.ceil((nextAt-Date.now())/1000));next.textContent='Next scan: '+s+'s';if(s===0){nextAt=Date.now()+15000;scan()}}},1000);
scan();
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return
    def send_json(self, obj, code=200):
        body=json.dumps(obj,ensure_ascii=False).encode()
        self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if self.path in ('/','/index.html'):
            body=HTML.encode(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body); return
        if self.path.startswith('/api/scan'):
            data=scan(); ok=sum(1 for x in data if not x.get('error')); self.send_json({'data':data,'meta':{'ok':ok,'total':len(data)}}); return
        if self.path.startswith('/api/test'):
            self.send_json({'ok':telegram_send('✅ Intraday AI Telegram test successful.')}); return
        if self.path.startswith('/api/settings'):
            self.send_json(load_settings()); return
        self.send_error(404)
    def do_POST(self):
        if self.path.startswith('/api/settings'):
            n=int(self.headers.get('Content-Length','0') or 0); raw=self.rfile.read(n)
            try: s=json.loads(raw.decode() or '{}')
            except Exception: s={}
            save_settings({'bot_token':str(s.get('bot_token','')).strip(),'chat_id':str(s.get('chat_id','')).strip(),'alerts':bool(s.get('alerts',True))})
            self.send_json({'ok':True}); return
        self.send_error(404)


def main():
    port=int(os.environ.get('PORT','10000'))
    server=ThreadingHTTPServer(('0.0.0.0',port),Handler)
    print(f'Intraday AI running on port {port}',flush=True)
    server.serve_forever()

if __name__=='__main__':
    main()
