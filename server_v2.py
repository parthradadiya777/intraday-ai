import json
import os
import threading
import time

import server
from nsemine import live


def _clean_number(v):
    try:
        return float(v)
    except Exception:
        return None


class NiftyHandler(server.FastHandler):
    def do_GET(self):
        path, _, query = self.path.partition("?")

        if path == "/api/nifty50":
            try:
                df = live.get_index_constituents_live_snapshot("NIFTY 50")
                if df is None or len(df) == 0:
                    self.send_json({"ok": False, "error": "NIFTY 50 constituent data unavailable"}, 503)
                    return

                rows = []
                for _, r in df.iterrows():
                    symbol = str(r.get("symbol", "")).strip()
                    if not symbol:
                        continue
                    rows.append({
                        "symbol": symbol,
                        "price": _clean_number(r.get("ltp")),
                        "change": _clean_number(r.get("changepct")),
                        "weightage": _clean_number(r.get("weightage")),
                        "volume": _clean_number(r.get("volume")),
                        "turnover": _clean_number(r.get("turnover")),
                        "source": "NSE NIFTY 50",
                    })

                self.send_json({"ok": True, "rows": rows, "count": len(rows)})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)[:180]}, 503)
            return

        if path == "/api/nifty50/index":
            try:
                q = live.get_index_live_price("NIFTY 50")
                if not q:
                    self.send_json({"ok": False, "error": "NIFTY 50 index data unavailable"}, 503)
                    return
                self.send_json({
                    "ok": True,
                    "data": {
                        "symbol": "NIFTY 50",
                        "price": _clean_number(q.get("close")),
                        "change": _clean_number(q.get("changepct")),
                        "open": _clean_number(q.get("open")),
                        "high": _clean_number(q.get("high")),
                        "low": _clean_number(q.get("low")),
                        "previous_close": _clean_number(q.get("previous_close")),
                    }
                })
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)[:180]}, 503)
            return

        return super().do_GET()


# Add a dedicated NIFTY 50 panel without rewriting the existing scanner UI.
html = server.app_auto.HTML
panel = r'''
<div class="panel" id="nifty50Panel">
  <h2>🇮🇳 NIFTY 50</h2>
  <div id="niftyIndex" class="nifty-index">Loading NIFTY 50…</div>
  <div class="searchrow">
    <input id="niftySearch" class="search" placeholder="🔍 Search NIFTY 50 stock">
    <span id="niftyInfo" class="searchinfo"></span>
  </div>
  <div class="tablewrap">
    <table>
      <thead><tr>
        <th>Stock</th><th>Price</th><th>Change</th><th>Weight</th><th>Volume</th><th>Turnover</th>
      </tr></thead>
      <tbody id="niftyRows"><tr><td colspan="6" class="empty">Loading…</td></tr></tbody>
    </table>
  </div>
</div>
'''
html = html.replace('<div class="wrap">', '<div class="wrap">' + panel, 1)

extra_css = r'''
<style>
.nifty-index{display:flex;gap:18px;align-items:center;flex-wrap:wrap;background:#f6f8fa;border-radius:12px;padding:14px;margin-bottom:14px}
.nifty-main{font-size:24px;font-weight:900}.nifty-change{font-size:18px;font-weight:800}
.nifty-meta{font-size:12px;color:#687386}
</style>
'''
html = html.replace('</style></head>', extra_css + '</style></head>', 1)

extra_js = r'''
let NIFTY50=[];

function niftyMoney(x){
  return x==null?'—':'₹'+Number(x).toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2});
}
function niftyRender(){
  const q=($('niftySearch').value||'').trim().toUpperCase();
  const rows=NIFTY50.filter(x=>!q||x.symbol.toUpperCase().includes(q));
  $('niftyInfo').textContent='Showing '+rows.length+' of '+NIFTY50.length+' NIFTY 50 stocks';
  $('niftyRows').innerHTML=rows.map(x=>{
    const ch=x.change==null?null:Number(x.change);
    const cls=ch!=null?(ch>=0?'green':'red'):'';
    return '<tr class="stockrow" onclick="openStock(\''+encodeURIComponent(x.symbol)+'\')">'+
      '<td><b>'+x.symbol+'</b></td>'+
      '<td>'+niftyMoney(x.price)+'</td>'+
      '<td class="'+cls+'">'+(ch==null?'—':ch.toFixed(2)+'%')+'</td>'+
      '<td>'+(x.weightage==null?'—':Number(x.weightage).toFixed(2)+'%')+'</td>'+
      '<td>'+(x.volume==null?'—':Number(x.volume).toLocaleString('en-IN'))+'</td>'+
      '<td>'+(x.turnover==null?'—':Number(x.turnover).toLocaleString('en-IN'))+'</td>'+
      '</tr>';
  }).join('')||'<tr><td colspan="6" class="empty">No matching NIFTY 50 stock.</td></tr>';
}
async function loadNifty50(){
  try{
    const [ir,cr]=await Promise.all([fetch('/api/nifty50/index'),fetch('/api/nifty50')]);
    const ij=await ir.json(), cj=await cr.json();
    if(ij.ok){
      const d=ij.data, ch=Number(d.change||0);
      $('niftyIndex').innerHTML=
        '<span class="nifty-main">NIFTY 50 '+niftyMoney(d.price)+'</span>'+
        '<span class="nifty-change '+(ch>=0?'green':'red')+'">'+(ch>=0?'+':'')+ch.toFixed(2)+'%</span>'+
        '<span class="nifty-meta">O '+niftyMoney(d.open)+' · H '+niftyMoney(d.high)+' · L '+niftyMoney(d.low)+' · Prev '+niftyMoney(d.previous_close)+'</span>';
    }else{
      $('niftyIndex').textContent='NIFTY 50 index data unavailable';
    }
    if(cj.ok){
      NIFTY50=cj.rows||[];
      niftyRender();
    }else{
      $('niftyRows').innerHTML='<tr><td colspan="6" class="empty">'+(cj.error||'NIFTY 50 data unavailable')+'</td></tr>';
    }
  }catch(e){
    $('niftyIndex').textContent='NIFTY 50 connection error';
    $('niftyRows').innerHTML='<tr><td colspan="6" class="empty">Unable to load NIFTY 50 data.</td></tr>';
  }
}
$('niftySearch').addEventListener('input',niftyRender);
loadNifty50();
setInterval(loadNifty50,15000);
'''
html = html.replace('</script></body>', extra_js + '</script></body>', 1)
server.app_auto.HTML = html

# Use the extended handler for the same existing server/scan implementation.
server.app_auto.Handler = NiftyHandler

if __name__ == "__main__":
    threading.Thread(target=server.app_auto.auto_loop, daemon=True).start()
    server.start_background_scan(force=True)
    port = int(os.environ.get("PORT", "10000"))
    server.app_auto.ThreadingHTTPServer(("0.0.0.0", port), NiftyHandler).serve_forever()
