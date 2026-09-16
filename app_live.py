import json, os, threading, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo
try:
    from nsemine import live
except Exception:
    live = None

VERSION = "2026.09.16-fix1"

def now_ist(): return datetime.now(ZoneInfo("Asia/Kolkata"))
def market_state():
    d=now_ist(); m=d.hour*60+d.minute
    if d.weekday()>=5:return "CLOSED"
    if m<555:return "PRE-OPEN"
    return "OPEN" if m<=930 else "CLOSED"

STATE={"rows":[],"ts":0,"last_error":"","source":"—","lock":threading.Lock()}

def nse_direct():
    url="https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20500"
    req=urllib.request.Request(url,headers={
        "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/153 Safari/537.36",
        "Accept":"application/json,text/plain,*/*","Accept-Language":"en-IN,en;q=0.9","Referer":"https://www.nseindia.com/market-data/live-equity-market"
    })
    with urllib.request.urlopen(req,timeout=12) as r: raw=r.read()
    obj=json.loads(raw.decode("utf-8")); data=obj.get("data",[])
    rows=[]
    for x in data:
        sym=str(x.get("symbol","")).strip(); p=x.get("lastPrice"); ch=x.get("pChange"); vol=x.get("totalTradedVolume",0)
        if not sym or p is None or ch is None: continue
        rows.append({"symbol":sym,"price":float(p),"change":float(ch),"volume":int(float(vol or 0)),"source":"NSE"})
    if not rows: raise RuntimeError("NSE direct API returned no usable rows")
    return rows

def nsemine_snapshot():
    if live is None: raise RuntimeError("nsemine import unavailable")
    df=live.get_all_securities_live_snapshot(series="EQ")
    if df is None or len(df)==0: raise RuntimeError("nsemine returned no rows")
    out=[]
    for _,r in df.iterrows():
        try: out.append({"symbol":str(r["symbol"]).strip(),"price":float(r["close"]),"change":float(r["changepct"]),"volume":int(float(r.get("volume",0) or 0)),"source":"NSE"})
        except Exception: pass
    if not out: raise RuntimeError("nsemine rows were unusable")
    return out

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
    ag,al=sum(g)/n,sum(l)/n
    return 100 if al==0 else 100-100/(1+ag/al)

def score_rows(rows):
    mv=max([x["volume"] for x in rows] or [1])
    for x in rows:
        x["score"]=round(max(0,min(100,50+max(-25,min(25,x["change"]*8))+min(10,x["volume"]/mv*10))),1)
        x["signal"]="BUY" if x["change"]>=2 else "SELL" if x["change"]<=-2 else "WAIT"
        x.update({"rsi":None,"ema9":None,"ema21":None,"momentum":None,"target":round(x["price"]*1.015,2),"sl":round(x["price"]*.99,2)})
    if live:
        cand=sorted(rows,key=lambda z:(abs(z["change"]),z["volume"]),reverse=True)[:10]
        def tech(x):
            try:
                df=live.get_stock_intraday_tick_by_tick_data(x["symbol"],candle_interval=5)
                cols={str(c).lower():c for c in df.columns}; cc=cols.get("close") or cols.get("last")
                a=[float(v) for v in df[cc].tolist() if v is not None]
                if len(a)<25:return None
                p=a[-1];e9,e21=ema(a,9),ema(a,21);rr=rsi(a);mom=(p/a[-2]-1)*100
                bull=(25 if p>e9 else 0)+(25 if p>e21 else 0)+(20 if e9>e21 else 0)+(15 if rr is not None and 52<=rr<=68 else 0)+(15 if mom>0 else 0)
                bear=(25 if p<=e9 else 0)+(25 if p<=e21 else 0)+(20 if e9<=e21 else 0)+(15 if rr is not None and 32<=rr<=48 else 0)+(15 if mom<0 else 0)
                return {"price":round(p,2),"rsi":round(rr,1) if rr is not None else None,"ema9":round(e9,2),"ema21":round(e21,2),"momentum":round(mom,3),"score":round(max(0,min(100,50+bull-bear)),1),"signal":"BUY" if bull>=70 and bull-bear>=30 else "SELL" if bear>=70 and bear-bull>=30 else "WAIT"}
            except Exception:return None
        with ThreadPoolExecutor(max_workers=3) as ex:
            fs={ex.submit(tech,x):x for x in cand}
            for f in as_completed(fs):
                t=f.result()
                if t:fs[f].update(t)
    return sorted(rows,key=lambda z:z["score"],reverse=True)

def scan(force=False):
    if not force and STATE["rows"] and time.time()-STATE["ts"]<60:return STATE["rows"]
    with STATE["lock"]:
        if not force and STATE["rows"] and time.time()-STATE["ts"]<60:return STATE["rows"]
        errs=[]
        try: rows=nse_direct(); source="NSE direct"
        except Exception as e:
            errs.append("NSE direct: "+str(e)[:100])
            try: rows=nsemine_snapshot(); source="NSE via nsemine"
            except Exception as e:
                errs.append("nsemine: "+str(e)[:100]); raise RuntimeError(" | ".join(errs))
        rows=score_rows(rows); STATE.update(rows=rows,ts=time.time(),last_error="",source=source)
        return rows

class H(BaseHTTPRequestHandler):
    def _send(self,code,body,ctype="application/json"):
        b=body.encode();self.send_response(code);self.send_header("Content-Type",ctype);self.send_header("Cache-Control","no-store, no-cache, must-revalidate, max-age=0");self.send_header("Pragma","no-cache");self.send_header("Content-Length",str(len(b)));self.end_headers();self.wfile.write(b)
    def do_GET(self):
        p=urllib.parse.urlparse(self.path).path
        if p=="/health":return self._send(200,json.dumps({"ok":True,"version":VERSION,"source":STATE["source"],"last_error":STATE["last_error"]}))
        if p=="/api/state":
            try: rows=scan()
            except Exception as e: rows=[];STATE["last_error"]=str(e)[:500]
            return self._send(200,json.dumps({"ok":bool(rows),"version":VERSION,"market":market_state(),"rows":rows,"source":STATE["source"],"last_error":STATE["last_error"]}))
        if p=="/api/scan":
            try: rows=scan(True); return self._send(200,json.dumps({"ok":True,"version":VERSION,"rows":rows,"source":STATE["source"]}))
            except Exception as e: STATE["last_error"]=str(e)[:500]; return self._send(503,json.dumps({"ok":False,"version":VERSION,"error":STATE["last_error"]}))
        return self._send(200,HTML,"text/html; charset=utf-8")
    def log_message(self,*a):pass

HTML="""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Intraday AI</title><style>body{margin:0;background:#f3f5f8;color:#172033;font-family:Arial}.wrap{max-width:1500px;margin:auto;padding:18px}header{background:#101827;color:#fff;padding:20px 28px}h1{margin:0}.sub{opacity:.75}.bar,.cards{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.panel{background:#fff;padding:18px;border-radius:14px;margin:14px 0;box-shadow:0 2px 12px #0001}button,input{padding:10px;border:1px solid #ccd3dd;border-radius:8px}button{background:#111827;color:white;font-weight:800}.cards{display:grid;grid-template-columns:repeat(5,1fr)}.card{background:#f7f8fa;padding:13px;border-radius:10px}.num{display:block;font-size:22px;font-weight:800;margin-top:5px}table{width:100%;border-collapse:collapse}th,td{padding:8px;border-bottom:1px solid #eee;text-align:left;white-space:nowrap}th{font-size:11px;color:#697386}.buy{color:#087f3b}.sell{color:#b42318}.small{font-size:12px;color:#697386}@media(max-width:700px){.cards{grid-template-columns:repeat(2,1fr)}table{font-size:11px}}</style></head><body><header><h1>Intraday AI</h1><div class='sub'>NSE equity scanner • automatic monitoring • BUY / WAIT / SELL • build %VERSION%</div><div class='bar' style='margin-top:12px'><span id='market'>MARKET --</span><span id='source'>SOURCE --</span><span id='ver'>BUILD --</span></div></header><div class='wrap'><div class='panel'><div class='bar'><b>Investment Budget ₹</b><input id='budget' type='number' value='5000'><b>Max Loss/Trade ₹</b><input id='risk' type='number' value='500'><button onclick='scanNow()'>SCAN NSE</button><span id='msg'>Starting…</span></div></div><div class='panel'><div class='cards'><div class='card'>NSE Stocks<span id='stocks' class='num'>0</span></div><div class='card'>BUY<span id='buys' class='num buy'>0</span></div><div class='card'>SELL<span id='sells' class='num sell'>0</span></div><div class='card'>WAIT<span id='waits' class='num'>0</span></div><div class='card'>Best Score<span id='top' class='num'>—</span></div></div></div><div class='panel'><b>STATUS</b><div id='guide'>Loading live NSE data…</div></div><div class='panel' style='overflow:auto'><table><thead><tr><th>Stock</th><th>Price</th><th>Change</th><th>RSI</th><th>EMA9</th><th>EMA21</th><th>Volume</th><th>Score</th><th>Signal</th><th>Budget Qty</th><th>Risk Qty</th><th>Target</th><th>SL</th></tr></thead><tbody id='rows'></tbody></table></div></div><script>const V='%VERSION%';function qty(p){let b=+budget.value||0,r=+risk.value||0;return[p?Math.floor(b/p):0,p?Math.floor(r/(p*.01)):0]}function render(a){stocks.textContent=a.length;buys.textContent=a.filter(x=>x.signal==='BUY').length;sells.textContent=a.filter(x=>x.signal==='SELL').length;waits.textContent=a.filter(x=>x.signal==='WAIT').length;top.textContent=a.length?a[0].score:'—';rows.innerHTML=a.slice(0,250).map(x=>{let q=qty(x.price);return `<tr><td><b>${x.symbol}</b></td><td>₹${x.price?.toFixed?.(2)??'—'}</td><td>${x.change?.toFixed?.(2)??'—'}%</td><td>${x.rsi??'—'}</td><td>${x.ema9??'—'}</td><td>${x.ema21??'—'}</td><td>${x.volume??0}</td><td>${x.score}</td><td class='${x.signal.toLowerCase()}'>${x.signal}</td><td>${q[0]}</td><td>${q[1]}</td><td>₹${x.target??'—'}</td><td>₹${x.sl??'—'}</td></tr>`}).join('')}async function scanNow(){msg.textContent='Scanning NSE…';try{let r=await fetch('/api/scan?x='+Date.now(),{cache:'no-store'}),x=await r.json();if(!x.ok)throw Error(x.error||'NSE scan failed');render(x.rows);source.textContent='SOURCE '+x.source;ver.textContent='BUILD '+V;msg.textContent='Loaded '+x.rows.length+' stocks';guide.textContent='Live scan loaded successfully. Signals are informational and not guaranteed.'}catch(e){msg.textContent='NSE scan failed';guide.textContent=e.message}}async function init(){try{let r=await fetch('/api/state?x='+Date.now(),{cache:'no-store'}),x=await r.json();market.textContent='MARKET '+x.market;source.textContent='SOURCE '+(x.source||'—');ver.textContent='BUILD '+x.version;if(x.ok)render(x.rows);else guide.textContent=x.last_error||'NSE data unavailable';}catch(e){guide.textContent='Connection error: '+e}}init();setInterval(init,60000)</script></body></html>""".replace('%VERSION%',VERSION)

if __name__=='__main__':
    port=int(os.environ.get('PORT','10000')); ThreadingHTTPServer(('0.0.0.0',port),H).serve_forever()
