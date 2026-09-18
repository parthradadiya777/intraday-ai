# Compatibility entrypoint for Render deployments running python app.py.
# Applies the stock-detail UI patch, then starts the same scanner server.

import os
import re
import threading
import server


def patch_stock_modal():
    html = server.app_auto.HTML

    # Remove the chart from the stock-detail modal.
    css = '''
    <style>
      .chart-toolbar,#priceChart,#chartStatus,.chart-note{display:none!important}
      .ai-analysis{margin-top:18px;border-top:1px solid #e3e8ee;padding-top:18px}
      .ai-head{display:flex;align-items:center;gap:10px;margin-bottom:12px}
      .live-pill{background:#dff7e8;color:#07833a;border-radius:18px;padding:5px 10px;font-size:12px;font-weight:800}
      .analysis-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
      .analysis-box{background:#f6f8fa;border-radius:10px;padding:14px;text-align:center}
      .analysis-box b{display:block;font-size:11px;color:#687386}
      .analysis-box span{display:block;font-size:24px;font-weight:900;margin-top:5px}
      .analysis-reason{background:#f2f6fc;border-radius:10px;padding:13px;margin-top:10px;color:#334155}
      .trade-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:10px}
      .trade-box{background:#f6f8fa;border-radius:9px;padding:10px}
      .trade-box b{display:block;font-size:11px;color:#687386}
      .trade-box span{font-size:16px;font-weight:800}
      @media(max-width:600px){.analysis-grid{grid-template-columns:1fr 1fr}.trade-grid{grid-template-columns:1fr 1fr}}
    </style>
    '''
    html = html.replace('</head>', css + '</head>', 1)

    # Put AI Analysis immediately below the stock header and before options.
    marker = '<div id="details" class="detailgrid"></div>'
    analysis = '''
    <div class="ai-analysis">
      <div class="ai-head">
        <h3 style="margin:0">🧠 AI ANALYSIS</h3>
        <span class="live-pill">LIVE</span>
      </div>
      <div id="analysisCards"></div>
    </div>
    '''
    html = html.replace(marker, analysis + marker, 1)

    # Disable chart network work entirely; the modal is now analysis + options.
    html += '''
    <script>
      window.loadChart = async function(){};
    </script>
    '''

    # Replace the old generic detail grid with a cleaner analysis renderer.
    old = r"""$('details').innerHTML=items.map(a=>'<div class="detail"><b>'+a[0]+'</b><span>'+a[1]+'</span></div>').join('');$('dreason').textContent=x.ai_reason||'No additional AI explanation available.'"""
    new = r"""$('analysisCards').innerHTML='<div class="analysis-grid"><div class="analysis-box"><b>AI SCORE</b><span>'+val(x.score)+'</span></div><div class="analysis-box"><b>CONFIDENCE</b><span>'+val(x.ai_confidence)+'%</span></div><div class="analysis-box"><b>SIGNAL</b><span>'+val(x.signal)+'</span></div></div><div class="trade-grid"><div class="trade-box"><b>LTP</b><span>'+money(x.price)+'</span></div><div class="trade-box"><b>CHANGE</b><span class="'+(Number(x.change)>=0?'green':'red')+'">'+val(x.change)+'%</span></div><div class="trade-box"><b>MARKET</b><span>'+val(x.market_trend)+'</span></div><div class="trade-box"><b>SECTOR</b><span>'+val(x.sector)+'</span></div><div class="trade-box"><b>TARGET</b><span class="green">'+money(x.target)+'</span></div><div class="trade-box"><b>STOP LOSS</b><span class="red">'+money(x.sl)+'</span></div><div class="trade-box"><b>RSI</b><span>'+val(x.rsi)+'</span></div><div class="trade-box"><b>VWAP</b><span>'+money(x.vwap)+'</span></div><div class="trade-box"><b>ADX</b><span>'+val(x.adx)+'</span></div><div class="trade-box"><b>REL VOLUME</b><span>'+val(x.relative_volume)+'</span></div></div><div class="analysis-reason"><b>AI Confirmations</b><div style="margin-top:7px">VWAP '+(x.confirmations&&x.confirmations.vwap?'✓':'✕')+' · EMA '+(x.confirmations&&x.confirmations.ema?'✓':'✕')+' · RSI '+(x.confirmations&&x.confirmations.rsi?'✓':'✕')+' · MACD '+(x.confirmations&&x.confirmations.macd?'✓':'✕')+' · ADX '+(x.confirmations&&x.confirmations.adx?'✓':'✕')+' · Volume '+(x.confirmations&&x.confirmations.relative_volume?'✓':'✕')+' · Market '+(x.market_confirmed?'✓':'✕')+' · Sector '+(x.sector_confirmed?'✓':'✕')+'</div></div><div class="analysis-reason"><b>AI Reason</b><div style="margin-top:7px">'+(x.ai_reason||'Live NSE snapshot with multi-factor refinement.')+'</div></div>';$('details').innerHTML='';$('dreason').textContent=''"""
    if old in html:
        html = html.replace(old, new, 1)

    server.app_auto.HTML = html


patch_stock_modal()

# Start the same server that app.py previously launched.
threading.Thread(target=server.app_auto.auto_loop, daemon=True).start()
threading.Thread(target=server.refresh_nifty_cache, daemon=True).start()
server.start_background_scan(force=True)

port = int(os.environ.get('PORT', '10000'))
server.app_auto.ThreadingHTTPServer(('0.0.0.0', port), server.NiftyHandler).serve_forever()
