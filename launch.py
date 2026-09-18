import json, time, threading, urllib.parse
from http import HTTPStatus

import enhanced_server
import server

LIVE_CACHE = {}
LIVE_LOCK = threading.Lock()
LIVE_TTL = 1.0


def _live_quote(symbol):
    symbol = str(symbol or "").upper().strip()
    if not symbol:
        return None
    now = time.time()
    with LIVE_LOCK:
        hit = LIVE_CACHE.get(symbol)
        if hit and now - hit["ts"] < LIVE_TTL:
            return dict(hit["data"])
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
            symbols = list(server.NIFTY_SYMBOLS)
            by_symbol = {}
            for r in server.app_auto.STATE.get("rows", []) or []:
                s = server._clean_nifty_symbol(r.get("symbol"))
                if s in symbols and r.get("price") is not None:
                    by_symbol[s] = {
                        "symbol": s,
                        "price": r.get("price"),
                        "change": r.get("change"),
                        "weightage": r.get("weightage"),
                        "volume": r.get("volume"),
                        "turnover": r.get("turnover")
                    }

            # If the scanner has not completed, use the raw NSE snapshot.
            if len(by_symbol) < 40:
                try:
                    df = server.app_auto.get_snapshot()
                    if df is not None and len(df):
                        for _, r in df.iterrows():
                            s = server._clean_nifty_symbol(r.get("symbol"))
                            if s in symbols:
                                by_symbol[s] = {
                                    "symbol": s,
                                    "price": r.get("close", r.get("ltp")),
                                    "change": r.get("changepct", r.get("change")),
                                    "weightage": r.get("weightage"),
                                    "volume": r.get("volume"),
                                    "turnover": r.get("turnover", r.get("traded_value"))
                                }
                except Exception:
                    pass

            rows = [by_symbol.get(s, {
                "symbol": s, "price": None, "change": None,
                "weightage": None, "volume": None, "turnover": None
            }) for s in symbols]
            valid = [r for r in rows if r.get("price") is not None]
            breadth = {
                "advance": sum(1 for r in valid if float(r.get("change") or 0) > 0),
                "decline": sum(1 for r in valid if float(r.get("change") or 0) < 0),
                "unchanged": sum(1 for r in valid if float(r.get("change") or 0) == 0)
            }
            self._json({
                "ok": True, "rows": rows, "count": len(rows),
                "breadth": breadth,
                "error": "" if valid else "Waiting for NSE snapshot",
                "ts": server.app_auto.STATE.get("ts", 0)
            }, 200)
            return

        if path == "/api/nifty50/index":
            try:
                q = server.live.get_index_live_price("NIFTY 50") or {}
                if q:
                    self._json({
                        "ok": True,
                        "data": {
                            "price": q.get("close"),
                            "change": q.get("changepct"),
                            "changepct": q.get("changepct"),
                            "open": q.get("open"),
                            "high": q.get("high"),
                            "low": q.get("low"),
                            "previous_close": q.get("previous_close")
                        }
                    }, 200)
                    return
            except Exception:
                pass
            self._json({"ok": False, "error": "NIFTY 50 index unavailable"}, 503)
            return

        return super().do_GET()


# Keep the existing NIFTY panel, but make its chart price visibly update
# every second from /api/live_quote instead of waiting for a 5-second candle
# refresh. This is still NSE web/scrape data, not an exchange tick feed.
html = server.app_auto.HTML
live_js = r'''
<script>
(function(){
  let livePriceTimer=null, lastLiveTime=0;
  async function tickLivePrice(){
    if(!window.activeChartSymbol || !window.activeLineSeries) return;
    const modal=document.getElementById('modal');
    if(!modal || modal.style.display!=='flex') return;
    try{
      const r=await fetch('/api/live_quote?symbol='+encodeURIComponent(window.activeChartSymbol)+'&t='+Date.now(),{cache:'no-store'});
      const j=await r.json();
      if(!j.ok || j.price==null) return;
      const now=Math.floor(Date.now()/1000);
      const t=Math.max(now,lastLiveTime+1);
      lastLiveTime=t;
      window.activeLineSeries.update({time:t,value:Number(j.price)});
      const s=document.getElementById('chartStatus');
      if(s) s.textContent='LIVE LTP ₹'+Number(j.price).toFixed(2)+' • NSE live quote • updating';
    }catch(e){}
  }
  livePriceTimer=setInterval(tickLivePrice,1000);
  window.addEventListener('beforeunload',()=>{if(livePriceTimer)clearInterval(livePriceTimer)});
})();
</script>
'''
# Expose chart globals so the live ticker can update the existing chart.
html = html.replace(
    "let activeChartSymbol='', activeChart=null, activeCandleSeries=null, activeVolumeSeries=null, activeLineSeries=null;",
    "let activeChartSymbol='', activeChart=null, activeCandleSeries=null, activeVolumeSeries=null, activeLineSeries=null; window.activeChartSymbol=''; window.activeLineSeries=null;"
)
html = html.replace(
    "async function openStock(sym){sym=decodeURIComponent(sym);activeChartSymbol=sym;",
    "async function openStock(sym){sym=decodeURIComponent(sym);activeChartSymbol=sym;window.activeChartSymbol=sym;"
)
html = html.replace(
    "activeLineSeries=activeChart.addLineSeries({",
    "activeLineSeries=activeChart.addLineSeries({"
)
html = html.replace(
    "      });\n      window.addEventListener('resize',()=>{if(activeChart)",
    "      }); window.activeLineSeries=activeLineSeries;\n      window.addEventListener('resize',()=>{if(activeChart)"
)
html = html.replace("</script></body></html>", live_js + "</script></body></html>")
server.app_auto.HTML = html


def main():
    import threading
    threading.Thread(target=server.app_auto.auto_loop, daemon=True).start()
    threading.Thread(target=server.refresh_nifty_cache, daemon=True).start()
    threading.Thread(target=enhanced_server.paper_loop, daemon=True).start()
    server.start_background_scan(force=True)
    port = int(__import__("os").environ.get("PORT", "10000"))
    server.app_auto.ThreadingHTTPServer(("0.0.0.0", port), LiveHandler).serve_forever()


if __name__ == "__main__":
    main()
