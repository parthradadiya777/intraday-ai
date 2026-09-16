import re, os, runpy

APP = 'app.py'
s = open(APP, encoding='utf-8').read()

# Reduce NSE request pressure: keep a successful scan usable for 90 seconds.
s = s.replace("time.time() - CACHE['ts'] < 45", "time.time() - CACHE['ts'] < 90")

# Never turn a temporary NSE rate-limit/network failure into a blank UI/error.
old = """            except Exception as e:\n                self.send_json({'ok': False, 'error': 'NSE data unavailable: ' + str(e)[:180]}, 503)\n            return\n"""
new = """            except Exception as e:\n                # If NSE temporarily rate-limits or drops a request, serve the last\n                # successful NSE scan instead of showing a scan failure to the user.\n                if CACHE.get('rows'):\n                    self.send_json({'ok': True, 'rows': CACHE['rows'], 'market': market_state(),\n                                    'time': now_ist().strftime('%I:%M:%S %p'), 'stale': True})\n                else:\n                    self.send_json({'ok': False, 'error': 'NSE data unavailable: ' + str(e)[:180]}, 503)\n            return\n"""
if old in s:
    s = s.replace(old, new)

# Avoid rebuilding the complete NSE master list on every scan. Refresh it at most once per day.
if "UNIVERSE_CACHE =" not in s:
    s = s.replace("CACHE = {'ts': 0, 'rows': [], 'universe': []}\n", "CACHE = {'ts': 0, 'rows': [], 'universe': []}\nUNIVERSE_CACHE = {'ts': 0, 'symbols': []}\n")

old_universe = """def get_universe():\n    # NSE master list; fallback to the live NSE snapshot if the master endpoint is unavailable.\n    try:\n        df = nse.get_all_equities_list()\n        if df is not None and len(df):\n            df = df[df['series'].astype(str).str.upper().eq('EQ')]\n            return sorted(set(df['symbol'].astype(str).str.strip()))\n    except Exception:\n        pass\n    return []\n"""
new_universe = """def get_universe():\n    # NSE master list is stable during the trading day. Cache it so every scan\n    # does not make another archive/master request.\n    import time\n    if UNIVERSE_CACHE['symbols'] and time.time() - UNIVERSE_CACHE['ts'] < 86400:\n        return UNIVERSE_CACHE['symbols']\n    try:\n        df = nse.get_all_equities_list()\n        if df is not None and len(df):\n            df = df[df['series'].astype(str).str.upper().eq('EQ')]\n            symbols = sorted(set(df['symbol'].astype(str).str.strip()))\n            if symbols:\n                UNIVERSE_CACHE['symbols'] = symbols\n                UNIVERSE_CACHE['ts'] = time.time()\n                return symbols\n    except Exception:\n        pass\n    return UNIVERSE_CACHE['symbols'] or []\n"""
if old_universe in s:
    s = s.replace(old_universe, new_universe)

open(APP, 'w', encoding='utf-8').write(s)
runpy.run_path(APP, run_name='__main__')
