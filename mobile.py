import os
import threading
import server
import app_auto

# Mobile-first UI layer. Keep the scanner/AI engine from server.py unchanged.
MOBILE_CSS = '''<style>
html,body{width:100%;overflow-x:hidden;-webkit-text-size-adjust:100%}
body{font-size:15px}
header{padding:16px 14px;position:sticky;top:0;z-index:20;box-shadow:0 2px 12px #0002}
header h1{font-size:24px}
.wrap{width:100%;max-width:100%;padding:10px}
.panel{padding:14px;margin-bottom:10px;border-radius:12px;box-shadow:0 1px 8px #0001}
h2{font-size:20px;margin:4px 0 14px}
.controls{display:grid;grid-template-columns:1fr 1fr;gap:9px}
.controls label{grid-column:1/-1;font-size:14px}
.controls input{width:100%;min-width:0;height:46px;font-size:17px}
.controls button{width:100%;height:46px;font-size:14px}
.status{gap:6px;margin-top:8px}
.chip{font-size:12px;padding:7px 9px}
.recommend{grid-template-columns:1fr;gap:9px}
.rec{padding:13px}
.symbol{font-size:19px}
.price{font-size:17px}
.meta{font-size:13px;line-height:1.35}
#recommendations .empty{padding:12px}
.table-wrap, .panel>div[style*="overflow:auto"]{width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch}
table{min-width:780px;font-size:13px}
th,td{padding:9px 7px}
.small{font-size:11px;line-height:1.4}
.hint{font-size:11px;line-height:1.4}
@media(max-width:600px){
  .controls{grid-template-columns:1fr}
  .controls label{grid-column:auto}
  .controls input,.controls button{grid-column:auto}
  .status{display:grid;grid-template-columns:1fr 1fr}
  .status .chip:last-child{grid-column:1/-1;text-align:center}
  .panel{padding:12px}
  .recommend .rec{border-radius:10px}
}
</style>'''

# Add mobile CSS before closing head.
if MOBILE_CSS not in app_auto.HTML:
    app_auto.HTML = app_auto.HTML.replace('</head>', MOBILE_CSS + '</head>')

if __name__ == '__main__':
    threading.Thread(target=app_auto.auto_loop, daemon=True).start()
    port = int(os.environ.get('PORT', '10000'))
    app_auto.ThreadingHTTPServer(('0.0.0.0', port), app_auto.Handler).serve_forever()
