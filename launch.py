import json, time, threading, urllib.parse
from http import HTTPStatus

import enhanced_server
import server

LIVE_CACHE = {}
LIVE_LOCK = threading.Lock()
LIVE_TTL = 0.8
# Temporary offline mode: keep the app fast/stable while the proper market-data
# provider is selected. Re-enable the data adapter later without changing UI.
NSE_DATA_ENABLED = True


def _live_quote(symbol):
    symbol = str(symbol or "").upper().strip()
    if not symbol:
        return None
    now = time.time()
    with LIVE_LOCK:
        hit = LIVE_CACHE.get(symbol)
        if hit and now - hit["ts"] < LIVE_TTL:
            return dict(hit["data"])
    # NSE/network adapter is intentionally disabled for now.
    if not NSE_DATA_ENABLED:
        return None

    try:
        q = server.live.get_stock_live_quotes(symbol) or {}
        price = q.get("close")
        if price is not None:
            data = {
                "ok": True,
                "symbol": symbol,
                "price": float(price),
                "change": q.get("changepct"),
                "datetime": q.get("datetime"),
                "source": "NSE live quote"
            }
            with LIVE_LOCK:
                LIVE_CACHE[symbol] = {"ts": now, "data": data}
            return data
    except Exception:
        pass

    # Scanner snapshot is the safe fallback when a direct quote request is
    # temporarily blocked/unavailable.
    for r in server.app_auto.STATE.get("rows", []) or []:
        if str(r.get("symbol", "")).upper().strip() == symbol and r.get("price") is not None:
            return {
                "ok": True, "symbol": symbol, "price": float(r["price"]),
                "change": r.get("change"), "datetime": None,
                "source": "NSE scanner snapshot"
            }
    return None


class LiveHandler(enhanced_server.EnhancedHandler):
    def do_GET(self):
        path, _, query = self.path.partition("?")

        if path == "/api/live_quote":
            qs = urllib.parse.parse_qs(query)
            symbol = (qs.get("symbol", [""])[0] or "").upper().strip()
            q = _live_quote(symbol)
            if q:
                self._json(q, 200)
            else:
                self._json({"ok": False, "error": "Live quote unavailable"}, 503)
            return

        if path == "/api/nifty50":
            # Keep the NIFTY universe available locally, but do not make any
            # network/NSE request until a proper market-data adapter is added.
            symbols = list(server.NIFTY_SYMBOLS)
            rows = [{
                "symbol": s, "price": None, "change": None,
                "weightage": None, "volume": None, "turnover": None
            } for s in symbols]
            self._json({
                "ok": True, "rows": rows, "count": 50,
                "breadth": {"advance": 0, "decline": 0, "unchanged": 0},
                "error": "Market data feed paused",
                "ts": 0
            }, 200)
            return

        if path == "/api/nifty50/index":
            self._json({"ok": False, "error": "Market data feed paused"}, 503)
            return

        return super().do_GET()


# UI hotfix: use the scanner state as the primary NIFTY 50 feed and keep a
# separate one-second live price trail in the chart. This avoids waiting for
# the slower dedicated constituent endpoint and avoids creating a new candle
# every second.
html = server.app_auto.HTML
\n# Hide the NIFTY 50 panel completely for the current scanner UI.\nhtml = html.replace("</head>", "<style>.nifty-panel{display:none!important}</style></head>", 1)\n
old_nifty_start = "async function loadNifty50(){
  try{
    // One source of truth: the dedicated NIFTY 50 endpoint. It always
    // returns exactly the 50 configured NIFTY constituents in fixed order.
    const r=await fetch('/api/nifty50?t='+Date.now(),{cache:'no-store'});
    const j=await r.json();
    const source=(j.rows||[]).filter(x=>x&&x.symbol);
    if(source.length){
      NIFTY50=source.slice(0,50).map(x=>({
        symbol:String(x.symbol).replace(/^NSE[:_]/i,'').replace(/-EQ$/i,''),
        price:x.price,
        change:x.change,
        weightage:x.weightage,
        volume:x.volume,
        turnover:x.turnover
      }));
      niftyRender();
      n$('niftyIndex').innerHTML='<span class="small">NIFTY 50 • Market data paused</span>';
      // Movers are only a summary; the table below is the single stock list.
      const adv=Number((j.breadth||{}).advance||0), dec=Number((j.breadth||{}).decline||0), unc=Number((j.breadth||{}).unchanged||0);
      n$('niftyBreadth').innerHTML='<div class="breadth-box"><b class="green">'+adv+'</b><span>ADVANCE</span></div><div class="breadth-box"><b class="red">'+dec+'</b><span>DECLINE</span></div><div class="breadth-box"><b>'+unc+'</b><span>UNCHANGED</span></div>';
      n$('niftyInfo').textContent='NIFTY 50 • '+source.length+'/50 symbols • Market data paused';
    } else {
      n$('niftyInfo').textContent='NIFTY 50 symbols ready • live data paused';
    }

    // Index quote is independent; it must never block the 50-stock table.
    try{
      const ir=await fetch('/api/nifty50/index?t='+Date.now(),{cache:'no-store'});
      const ij=await ir.json();
      if(ij.ok&&ij.data){
        const d=ij.data,ch=Number(d.changepct??d.change??0);
        n$('niftyIndex').innerHTML='<span class="nifty-main">NIFTY 50 '+niftyMoney(d.price)+'</span><span class="nifty-change '+(ch>=0?'green':'red')+'">'+(ch>=0?'+':'')+ch.toFixed(2)+'%</span><span class="nifty-meta">O '+niftyMoney(d.open)+' · H '+niftyMoney(d.high)+' · L '+niftyMoney(d.low)+' · Prev '+niftyMoney(d.previous_close)+'</span>';
      } else {
        n$('niftyIndex').innerHTML='<span class="small">NIFTY index waiting…</span>';
      }
    }catch(e){
      n$('niftyIndex').innerHTML='<span class="small">NIFTY index reconnecting…</span>';
    }
  }catch(e){
    n$('niftyInfo').textContent=NIFTY50.length?'Reconnecting • '+NIFTY50.length+' stocks':'Waiting for NSE data…';
  }
}
n$('niftySearch').addEventListener('input',niftyRender);
loadNifty50();setInterval(loadNifty50,15000);

startChartLiveRefresh();"
    end = html.index(end_marker, start)
    old_block = html[start:end]
    new_block = r"""
async function loadNifty50(){
  try{
    // Scanner already contains the filtered NIFTY 50 universe. Use it first.
    const sr=await fetch('/api/state?t='+Date.now(),{cache:'no-store'});
    const sj=await sr.json();
    const source=(sj.rows||[]).filter(x=>x&&x.symbol);
    if(source.length){
      NIFTY50=source.slice(0,50).map(x=>({
        symbol:String(x.symbol).replace(/^NSE[:_]/i,'').replace(/-EQ$/i,''),
        price:x.price,
        change:x.change,
        weightage:x.weightage,
        volume:x.volume,
        turnover:x.turnover
      }));
      niftyRender(); niftyMovers();
      const adv=NIFTY50.filter(x=>x.price!=null&&Number(x.change)>0).length;
      const dec=NIFTY50.filter(x=>x.price!=null&&Number(x.change)<0).length;
      const unc=NIFTY50.filter(x=>x.price!=null&&Number(x.change)==0).length;
      n$('niftyBreadth').innerHTML='<div class="breadth-box"><b class="green">'+adv+'</b><span>ADVANCE</span></div><div class="breadth-box"><b class="red">'+dec+'</b><span>DECLINE</span></div><div class="breadth-box"><b>'+unc+'</b><span>UNCHANGED</span></div>';
    } else {
      n$('niftyInfo').textContent='Waiting for NSE scanner data…';
    }

    // Index quote is optional; never let it block the 50-stock table.
    try{
      const ir=await fetch('/api/nifty50/index?t='+Date.now(),{cache:'no-store'});
      const ij=await ir.json();
      if(ij.ok&&ij.data){
        const d=ij.data,ch=Number(d.changepct??d.change??0);
        n$('niftyIndex').innerHTML='<span class="nifty-main">NIFTY 50 '+niftyMoney(d.close??d.price)+'</span><span class="nifty-change '+(ch>=0?'green':'red')+'">'+(ch>=0?'+':'')+ch.toFixed(2)+'%</span><span class="nifty-meta">O '+niftyMoney(d.open)+' · H '+niftyMoney(d.high)+' · L '+niftyMoney(d.low)+' · Prev '+niftyMoney(d.previous_close)+'</span>';
      } else {
        n$('niftyIndex').innerHTML='<span class="small">NIFTY index quote waiting…</span>';
      }
    }catch(e){
      n$('niftyIndex').innerHTML='<span class="small">NIFTY index reconnecting…</span>';
    }
  }catch(e){
    n$('niftyInfo').textContent=NIFTY50.length?'Live reconnecting • '+NIFTY50.length+' stocks':'Waiting for NSE data…';
  }
}
n$('niftySearch').addEventListener('input',niftyRender);
loadNifty50();setInterval(loadNifty50,2000);

"""
    html = html[:start] + new_block + html[end_marker:]

# Make chart globals available to a live ticker.
html = html.replace(
    "let activeChartSymbol='', activeChart=null, activeCandleSeries=null, activeVolumeSeries=null, activeLineSeries=null;",
    "let activeChartSymbol='', activeChart=null, activeCandleSeries=null, activeVolumeSeries=null, activeLineSeries=null; window.activeChartSymbol=''; window.activeLineSeries=null;"
)
html = html.replace(
    "async function openStock(sym){sym=decodeURIComponent(sym);activeChartSymbol=sym;",
    "async function openStock(sym){sym=decodeURIComponent(sym);activeChartSymbol=sym;window.activeChartSymbol=sym;"
)

# After the line series is created, expose it.
html = html.replace(
    "activeLineSeries=activeChart.addLineSeries({
        lineWidth:3, priceLineVisible:false, lastValueVisible:true
      });",
    "activeLineSeries=activeChart.addLineSeries({
        lineWidth:3, priceLineVisible:false, lastValueVisible:true
      }); window.activeLineSeries=activeLineSeries;"
)

# Replace the old 5-second chart refresh with a lightweight live quote ticker.
live_js = r"""
<script>
(function(){
  let liveTimer=null, livePoints=[], lastT=0, lastP=null;
  function resetLive(){livePoints=[];lastT=0;lastP=null;}
  async function tick(){
    if(!NSE_DATA_ENABLED || !window.activeChartSymbol || !window.activeLineSeries) return;
    const modal=document.getElementById('modal');
    if(!modal || modal.style.display!=='flex') return;
    try{
      const r=await fetch('/api/live_quote?symbol='+encodeURIComponent(window.activeChartSymbol)+'&t='+Date.now(),{cache:'no-store'});
      const j=await r.json();
      if(!j.ok || j.price==null) return;
      const p=Number(j.price);
      const now=Math.floor(Date.now()/1000);
      const t=Math.max(now,lastT+1);
      lastT=t; lastP=p;
      livePoints.push({time:t,value:p});
      if(livePoints.length>90) livePoints.shift();
      // Keep the live trail separate from candles: this gives a smooth,
      // continuously moving broker-style LTP line without fabricating candles.
      window.activeLineSeries.setData(livePoints);
      const s=document.getElementById('chartStatus');
      if(s) s.textContent='● LIVE LTP ₹'+p.toFixed(2)+' • market feed paused • '+new Date().toLocaleTimeString('en-IN');
    }catch(e){}
  }
  liveTimer=setInterval(tick,1000);
  document.addEventListener('visibilitychange',()=>{if(document.hidden) resetLive()});
})();
</script>
"""
html = html.replace("</script></body></html>", live_js + "</script></body></html>")
server.app_auto.HTML = html

def main():
    import threading
    # Do not start NSE polling/scanning while the market-data adapter is paused.
    # This keeps startup fast and avoids repeated network calls.
    if NSE_DATA_ENABLED:
        threading.Thread(target=server.app_auto.auto_loop, daemon=True).start()
        threading.Thread(target=server.refresh_nifty_cache, daemon=True).start()
        threading.Thread(target=enhanced_server.paper_loop, daemon=True).start()
        server.start_background_scan(force=True)
    port = int(__import__("os").environ.get("PORT", "10000"))
    server.app_auto.ThreadingHTTPServer(("0.0.0.0", port), LiveHandler).serve_forever()


if __name__ == "__main__":
    main()
