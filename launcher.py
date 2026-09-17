import os
import re

# Render runs this launcher so we can apply a small compatibility patch without
# changing the scanner/market-data implementation in app_auto.py.
path = os.path.join(os.path.dirname(__file__), 'app_auto.py')
source = open(path, encoding='utf-8').read()

# Retry Telegram alerts that previously failed (for example because the bot was
# configured after AUTO SYSTEM had already seen a signal). Previously any failed
# attempt permanently consumed the alert key.
old_alert = '''def alert_once(key, text):
    if any(x.get('key') == key for x in STATE['signals']):
        return False
    ok = telegram(text)
    STATE['signals'].append({'key': key, 'time': now_ist().strftime('%H:%M:%S'), 'text': text, 'sent': ok})
    STATE['signals'] = STATE['signals'][-100:]
    return ok
'''
new_alert = '''def alert_once(key, text):
    # Suppress duplicates only after a successful send. Failed sends are retried
    # on the next auto cycle after Telegram is configured/reachable.
    previous = next((x for x in reversed(STATE['signals']) if x.get('key') == key), None)
    if previous and previous.get('sent'):
        return False
    ok = telegram(text)
    STATE['signals'].append({'key': key, 'time': now_ist().strftime('%H:%M:%S'), 'text': text, 'sent': ok})
    STATE['signals'] = STATE['signals'][-100:]
    return ok
'''
if old_alert in source:
    source = source.replace(old_alert, new_alert, 1)

# Make Telegram failures visible in the TEST PHONE result while never exposing
# the bot token itself.
source = source.replace(
    "    except Exception:\n        return False\n\ndef ema(a, n):",
    "    except Exception as e:\n        STATE['last_error'] = 'Telegram error: ' + str(e)[:180]\n        return False\n\ndef ema(a, n):",
    1,
)
source = source.replace(
    "ok=telegram('✅ Intraday AI test alert — Telegram is connected.'); self.send_json({'ok':ok}); return",
    "ok=telegram('✅ Intraday AI test alert — Telegram is connected.'); self.send_json({'ok':ok,'error': '' if ok else STATE.get('last_error','Telegram send failed')}); return",
    1,
)

exec(compile(source, path, 'exec'), {'__name__': '__main__', '__file__': path})
