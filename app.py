import json, urllib.request, urllib.parse, webbrowser, threading, math, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import os


DATA_DIR = os.environ.get("DATA_DIR", "/var/data")
os.makedirs(DATA_DIR, exist_ok=True)
SETTINGS_FILE = os.path.join(DATA_DIR, "guide_settings.json")
LAST_ALERT = {}
LAST_GUIDE = {"action":"WAIT", "symbol":None, "score":0}

def load_settings():
    try:
        with open(SETTINGS_FILE,"r",encoding="utf-8") as f: return json.load(f)
    except Exception: return {"bot_token":"","chat_id":"","alerts":True}

def save_settings(s):
    with open(SETTINGS_FILE,"w",encoding="utf-8") as f: json.dump(s,f)

def telegram_send(text):
    s=load_settings()
    if not s.get("alerts") or not s.get("bot_token") or not s.get("chat_id"): return False
    try:
        url="https://api.telegram.org/bot"+s["bot_token"]+"/sendMessage"
        data=urllib.parse.urlencode({"chat_id":s["chat_id"],"text":text}).encode()
        req=urllib.request.Request(url,data=data,headers={"User-Agent":"IntradayAI"})
        with urllib.request.urlopen(req,timeout=8) as r: return r.status==200
    except Exception: return False

def alert_once(key,text):
    if LAST_ALERT.get(key): return
    if telegram_send(text): LAST_ALERT[key]=True

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

def yahoo(symbol):
    url=f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}.NS?range=1d&interval=1m"
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req,timeout=8) as r:
        return json.load(r)["chart"]["result"][0]

def ema(vals, n):
    if len(vals) < n: return None
    k=2/(n+1)
    e=sum(vals[:n])/n
    for x in vals[n:]: e=x*k+e*(1-k)
    return e

def rsi(vals, n=14):
    if len(vals)<n+1: return None
    gains=[]; losses=[]
    for a,b in zip(vals[-n-1:-1], vals[-n:]):
        d=b-a; gains.append(max(d,0)); losses.append(max(-d,0))
    ag=sum(gains)/n; al=sum(losses)/n
    if al==0: return 100
    return 100-(100/(1+ag/al))

def one(symbol):
    try:
        r=yahoo(symbol); q=r["indicators"]["quote"][0]
        close=[x for x in q.get("close",[]) if x is not None]
        high=[x for x in q.get("high",[]) if x is not None]
        low=[x for x in q.get("low",[]) if x is not None]
        vol=[x for x in q.get("volume",[]) if x is not None]
        if len(close)<25: raise ValueError()
        p=close[-1]; prev=close[-2]
        e9=ema(close,9); e21=ema(close,21); rr=rsi(close,14)
        avgvol=sum(vol[-20:])/len(vol[-20:]) if vol[-20:] else 0
        v=vol[-1] if vol else 0
        ch=(p/close[0]-1)*100
        mom=(p/prev-1)*100
        recent_high=max(high[-20:]); recent_low=min(low[-20:])
        # Intraday VWAP from available session candles.
        pv=sum(((h+l+c)/3)*vv for h,l,c,vv in zip(high,low,close,vol))
        tv=sum(vol)
        vwap=pv/tv if tv else p

        score=0
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
        # bearish points
        bear=0
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
                "rsi":round(rr,1) if rr is not None else None,"vwap":round(vwap,2)}
    except Exception:
        return {"symbol":symbol,"error":True}

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
        data=[f.result() for f in as_completed([ex.submit(one,s) for s in SYMBOLS])]
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

HTML = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Intraday AI — Tomorrow Ready</title>
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
<header><h1>Intraday AI — Tomorrow Ready</h1><div class="sub">Trend + VWAP + RSI + EMA + momentum + volume + market confirmation</div>
<div class="status"><span id="clock">🕐 --</span><span id="date">📅 --</span><span id="market">MARKET --</span><span id="last">Last data update: —</span><span id="next">Next scan: —</span></div></header>
<div class="wrap">
<div class="panel"><div class="controls"><b>Phone Alerts (Telegram)</b><input id="bot" placeholder="Bot Token" style="min-width:220px"><input id="chat" placeholder="Chat ID"><button onclick="saveAlerts()">SAVE ALERTS</button><button onclick="testAlert()">TEST PHONE</button></div><div class="small" style="margin-top:8px">The app itself will guide you. You do not need to come to ChatGPT for every BUY/WAIT decision. Keep the app running for alerts.</div></div>
<div class="panel"><div class="controls"><b>Investment Budget ₹</b><input id="budget" type="number" value="100000"><b>Max Loss/Trade ₹</b><input id="risk" type="number" value="500"><b>Max Trades</b><input id="maxtrades" type="number" value="2"><select id="mode"><option>ALL STOCKS</option><option>BUY ONLY</option><option>SELL ONLY</option><option>WAIT ONLY</option></select><button onclick="scan()">SCAN NSE</button><span id="msg">Starting…</span></div></div>
<div class="panel"><div class="cards"><div class="card">Stocks<span id="stocks" class="num">0</span></div><div class="card">BUY<span id="buys" class="num buy">0</span></div><div class="card">SELL<span id="sells" class="num sell">0</span></div><div class="card">WAIT<span id="waits" class="num wait">0</span></div><div class="card">Best Score<span id="topscore" class="num">—</span></div><div class="card">NIFTY Confirm<span id="nifty" class="num">—</span></div></div></div>
<div id="best" class="panel none"><b>🤖 TRADING GUIDE</b><div id="besttext">Waiting…</div><div id="reason" class="small" style="margin-top:7px"></div></div>
<div class="panel"><h3>Stock Scanner</h3><div style="overflow:auto"><table><thead><tr><th>Stock</th><th>Price</th><th>Change</th><th>RSI</th><th>VWAP</th><th>EMA9</th><th>EMA21</th><th>Volume</th><th>Score</th><th>Signal</th><th>Budget Qty</th><th>Risk Qty</th><th>Plan Qty</th><th>Target</th><th>SL</th></tr></thead><tbody id="rows"></tbody></table></div></div>
<div class="panel"><h3 id="chartTitle">Stock Chart — click a stock</h3><canvas id="chart"></canvas><div id="chartInfo" class="small"></div></div>
<div class="panel"><h3>Signal History</h3><table><thead><tr><th>Time</th><th>Stock</th><th>Signal</th><th>Score</th><th>Price</th></tr></thead><tbody id="history"></tbody></table></div>
<div class="panel warn"><b>Tomorrow's rule:</b> Before 9:15 AM the app should show WATCH/WAIT only. During market hours it can produce BUY/SELL only after multiple confirmations. Free Yahoo data may be delayed/cached/rate-limited; no signal guarantees profit.</div>
</div>
<script>
let nextAt=0,signalHistory=[];
function tick(){let d=new Date();clock.textContent='🕐 '+d.toLocaleTimeString('en-IN',{hour12:true});date.textContent='📅 '+d.toLocaleDateString('en-IN');let m=d.getHours()*60+d.getMinutes(),w=d.getDay();let open=w>=1&&w<=5&&m>=555&&m<=930;market.textContent=open?'🟢 MARKET OPEN':'🔴 MARKET CLOSED';if(nextAt)next.textContent='Next scan: '+Math.max(0,Math.ceil((nextAt-Date.now())/1000))+'s'}setInterval(tick,1000);tick();
function qtys(price){let b=+budget.value||0,r=+risk.value||0;let bq=Math.floor(b/price);let rq=Math.floor(r/(price*.01));return [bq,rq,Math.min(bq,rq)]}
async function scan(){msg.textContent='Scanning…';try{let d=await (await fetch('/api/scan?x='+Date.now())).json();let ok=d.filter(x=>!x.error);stocks.textContent=ok.length;buys.textContent=ok.filter(x=>x.signal==='BUY').length;sells.textContent=ok.filter(x=>x.signal==='SELL').length;waits.textContent=ok.filter(x=>x.signal==='WAIT').length;let top=ok.filter(x=>x.signal==='BUY').sort((a,b)=>b.score-a.score)[0];topscore.textContent=top?top.score:'—';if(top){let [bq,rq,pq]=qtys(top.price),t=top.price*1.015,sl=top.price*.99;best.className='panel best';besttext.innerHTML='<b>🟢 BUY NOW — '+top.symbol+'</b> | Entry ₹'+top.price.toFixed(2)+' | Target ₹'+t.toFixed(2)+' | SL ₹'+sl.toFixed(2)+' | Budget Qty '+bq+' | Risk Qty '+rq+' | Plan Qty '+pq}else{best.className='panel none';besttext.textContent='⚪ WAIT — no confirmed high-quality BUY setup right now.'}
let mode=document.getElementById('mode').value;let shown=ok.filter(x=>mode==='ALL STOCKS'||(mode==='BUY ONLY'&&x.signal==='BUY')||(mode==='SELL ONLY'&&x.signal==='SELL')||(mode==='WAIT ONLY'&&x.signal==='WAIT')).sort((a,b)=>b.score-a.score);rows.innerHTML=shown.map(x=>{let [bq,rq,pq]=qtys(x.price),t=x.price*1.015,sl=x.price*.99;return '<tr class="click" onclick="showChart(\\''+x.symbol+'\\')"><td><b>'+x.symbol+'</b></td><td>₹'+x.price+'</td><td>'+x.change+'%</td><td>'+x.rsi+'</td><td>₹'+x.vwap+'</td><td>₹'+x.ema9+'</td><td>₹'+x.ema21+'</td><td>'+x.volume.toLocaleString('en-IN')+'</td><td>'+x.score+'</td><td class="'+x.signal.toLowerCase()+'">'+x.signal+'</td><td>'+bq+'</td><td>'+rq+'</td><td>'+pq+'</td><td>'+(x.signal==='BUY'?'₹'+t.toFixed(2):'—')+'</td><td>'+(x.signal==='BUY'?'₹'+sl.toFixed(2):'—')+'</td></tr>'}).join('');let now=new Date().toLocaleTimeString('en-IN',{hour12:true});last.textContent='Last data update: '+now;nextAt=Date.now()+15000;msg.textContent='Updated '+ok.length+' stocks.';if(top){signalHistory.unshift({time:now,symbol:top.symbol,signal:'BUY',score:top.score,price:top.price});signalHistory=signalHistory.slice(0,20);historyEl()} }catch(e){msg.textContent='Data unavailable — check internet.'}}
function historyEl(){historyElx=document.getElementById('history');historyElx.innerHTML=signalHistory.map(x=>'<tr><td>'+x.time+'</td><td>'+x.symbol+'</td><td class="buy">'+x.signal+'</td><td>'+x.score+'</td><td>₹'+x.price+'</td></tr>').join('')}
async function showChart(s){chartTitle.textContent=s+' — 1 minute chart';try{let d=await (await fetch('/api/chart?symbol='+encodeURIComponent(s)+'&x='+Date.now())).json();draw(d.prices||[]);chartInfo.textContent='Chart update: '+new Date().toLocaleTimeString('en-IN',{hour12:true})}catch(e){draw([])}}
function draw(a){let c=document.getElementById('chart'),ctx=c.getContext('2d'),W=c.clientWidth,H=c.clientHeight,r=devicePixelRatio||1;c.width=W*r;c.height=H*r;ctx.scale(r,r);ctx.clearRect(0,0,W,H);if(!a.length){ctx.fillStyle='#fff';ctx.fillText('Chart data unavailable',20,30);return}let mn=Math.min(...a),mx=Math.max(...a),p=25;ctx.beginPath();a.forEach((v,i)=>{let x=p+i*(W-2*p)/(a.length-1),y=H-p-(v-mn)/(mx-mn||1)*(H-2*p);i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.strokeStyle='#65b7ff';ctx.lineWidth=2;ctx.stroke();ctx.fillStyle='#fff';ctx.fillText('Last ₹'+a[a.length-1].toFixed(2),10,20)}

async function guide(){
 try{
  let d=await (await fetch('/api/guide?x='+Date.now())).json();
  if(d.settings){bot.value=d.settings.bot_token||'';chat.value=d.settings.chat_id||''}
  if(d.state==="PRE-OPEN"){best.className="panel none";besttext.innerHTML="🟡 <b>PRE-OPEN</b> — Do not buy yet.";reason.textContent="Wait for market confirmation after 9:15 AM."}
  if(d.state==="CLOSED"){besttext.innerHTML="🔴 <b>MARKET CLOSED</b>";reason.textContent="Tomorrow the scanner will start automatically when the market opens."}
  if(d.state==="OPEN" && d.guide.action==="BUY"){best.className="panel best";besttext.innerHTML="🟢 <b>BUY NOW — "+d.guide.symbol+"</b>";reason.textContent="Multiple confirmations passed. Check the scanner's Entry / Target / SL before placing any order."}
  else if(d.state==="OPEN" && d.guide.action==="SELL"){best.className="panel warn";besttext.innerHTML="🔴 <b>SELL SETUP — "+d.guide.symbol+"</b>";reason.textContent="Bearish/short setup. This is not an EXIT signal for an existing long position."}
 }catch(e){}
}
async function saveAlerts(){await fetch('/api/save-alerts?bot_token='+encodeURIComponent(bot.value)+'&chat_id='+encodeURIComponent(chat.value));msg.textContent='Phone alerts saved.'}
async function testAlert(){let d=await (await fetch('/api/test-alert?x='+Date.now())).json();msg.textContent=d.ok?'Test alert sent to phone.':'Telegram connection failed — check Bot Token and Chat ID.'}
setInterval(guide,2000); guide();

setInterval(scan,15000);scan();
</script></body></html>"""

class Handler(BaseHTTPRequestHandler):
    def reply(self,code,body,ctype="text/html; charset=utf-8"):
        b=body.encode("utf-8");self.send_response(code);self.send_header("Content-Type",ctype);self.send_header("Content-Length",str(len(b)));self.end_headers();self.wfile.write(b)
    def do_GET(self):
        u=urllib.parse.urlparse(self.path)
        if u.path=="/": return self.reply(200,HTML)
        if u.path=="/health": return self.reply(200,"ok","text/plain; charset=utf-8")
        if u.path=="/api/scan": return self.reply(200,json.dumps(scan()),"application/json")
        if u.path=="/api/chart":
            s=urllib.parse.parse_qs(u.query).get("symbol",["RELIANCE"])[0]
            try:
                q=yahoo(s)["indicators"]["quote"][0]
                return self.reply(200,json.dumps({"prices":[x for x in q.get("close",[]) if x is not None]}),"application/json")
            except: return self.reply(200,'{"prices":[]}',"application/json")
        if u.path=="/api/guide":
            return self.reply(200,json.dumps({"state":market_state(),"guide":LAST_GUIDE,"settings":load_settings()}),"application/json")
        if u.path=="/api/test-alert":
            ok=telegram_send("🔔 Intraday AI TEST ALERT\nPhone alerts are connected.")
            return self.reply(200,json.dumps({"ok":ok}),"application/json")
        if u.path=="/api/save-alerts":
            q=urllib.parse.parse_qs(u.query)
            s={"bot_token":q.get("bot_token",[""])[0],"chat_id":q.get("chat_id",[""])[0],"alerts":True}
            save_settings(s)
            return self.reply(200,json.dumps({"ok":True}),"application/json")
        return self.reply(404,"Not found")

if __name__=="__main__":
    port=int(os.environ.get("PORT","10000"))
    host=os.environ.get("HOST","0.0.0.0")
    server=ThreadingHTTPServer((host,port),Handler)
    print(f"Intraday AI running on http://{host}:{port}/")
    if os.environ.get("OPEN_BROWSER","0")=="1":
        threading.Timer(1,lambda:webbrowser.open(f"http://127.0.0.1:{port}/")).start()
    server.serve_forever()
