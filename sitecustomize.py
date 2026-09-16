"""Startup hardening for the Render NSE scanner.
Python imports sitecustomize automatically before app.py, so this safely patches
small operational issues without replacing the working scanner code.
"""
import os

APP = os.path.join(os.path.dirname(__file__), 'app.py')
try:
    s = open(APP, encoding='utf-8').read()
    original = s

    # NSE website endpoints are rate-limited; reuse a successful market snapshot
    # longer than the browser's 60-second refresh cycle.
    s = s.replace("time.time() - CACHE['ts'] < 45", "time.time() - CACHE['ts'] < 90")

    # Reduce the expensive 5-minute confirmation calls from 30 to 12 most-active
    # stocks. The complete NSE universe still gets live price/volume scanning.
    s = s.replace("[:30]", "[:12]")
    s = s.replace("ThreadPoolExecutor(max_workers=5)", "ThreadPoolExecutor(max_workers=3)")

    # A temporary NSE throttle/network error must not erase the last good scan.
    old = """            except Exception as e:\n                self.send_json({'ok': False, 'error': 'NSE data unavailable: ' + str(e)[:180]}, 503)\n            return\n"""
    new = """            except Exception as e:\n                if CACHE.get('rows'):\n                    self.send_json({'ok': True, 'rows': CACHE['rows'], 'market': market_state(),\n                                    'time': now_ist().strftime('%I:%M:%S %p'), 'stale': True})\n                else:\n                    self.send_json({'ok': False, 'error': 'NSE data unavailable: ' + str(e)[:180]}, 503)\n            return\n"""
    if old in s:
        s = s.replace(old, new)

    # Dynamic universe count in the browser message instead of the old 65-stock text.
    s = s.replace("msg.textContent='Scanning NSE…'", "msg.textContent='Scanning complete NSE equity universe…'")
    s = s.replace("msg.textContent=`Scanned ${d.rows.filter(x=>!x.error).length}/${d.rows.length} NSE equity stocks`", "msg.textContent=`Scanned ${d.rows.filter(x=>!x.error).length}/${d.rows.length} NSE equity stocks`")
    s = s.replace("nextAt=Date.now()+45000", "nextAt=Date.now()+60000")

    if s != original:
        open(APP, 'w', encoding='utf-8').write(s)
except Exception:
    # Never prevent the application from starting because of this hardening file.
    pass
