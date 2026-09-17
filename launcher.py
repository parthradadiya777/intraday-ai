import os
import re

# Render runs this launcher so we can apply small compatibility patches while
# keeping the scanner/market-data implementation in app_auto.py intact.
path = os.path.join(os.path.dirname(__file__), 'app_auto.py')
source = open(path, encoding='utf-8').read()

# Retry Telegram alerts that failed before Telegram was configured/reachable.
old_alert = '''def alert_once(key, text):
    if any(x.get('key') == key for x in STATE['signals']):
        return False
    ok = telegram(text)
    STATE['signals'].append({'key': key, 'time': now_ist().strftime('%H:%M:%S'), 'text': text, 'sent': ok})
    STATE['signals'] = STATE['signals'][-100:]
    return ok
'''
new_alert = '''def alert_once(key, text):
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

# Make Telegram errors visible to the TEST PHONE button without exposing the token.
source = re.sub(
    r"(def telegram\(text\):.*?\n    except Exception:)\n        return False",
    r"\1 as e:\n        STATE['last_error'] = 'Telegram error: ' + str(e)[:180]\n        return False",
    source,
    count=1,
    flags=re.S,
)
source = source.replace(
    "ok=telegram('✅ Intraday AI test alert — Telegram is connected.'); self.send_json({'ok':ok}); return",
    "ok=telegram('✅ Intraday AI test alert — Telegram is connected.'); self.send_json({'ok':ok,'error': '' if ok else STATE.get('last_error','Telegram send failed')}); return",
    1,
)

# Update the budget/risk/Max Trades display immediately when the user types.
needle = "setInterval(state,15000);state();"
replacement = "['budget','risk','maxtrades'].forEach(id=>$(id).addEventListener('input',()=>state()));setInterval(state,15000);state();"
if needle in source:
    source = source.replace(needle, replacement, 1)

# Show the actual Telegram API error in the page instead of a generic failure.
old_test = "async function testAlert(){let r=await fetch('/api/test');let j=await r.json();$('msg').textContent=j.ok?'Test sent':'Test failed — check Telegram token/chat ID'}"
new_test = "async function testAlert(){let r=await fetch('/api/test');let j=await r.json();$('msg').textContent=j.ok?'Test sent ✅':('Telegram failed: '+(j.error||'check Bot Token and Chat ID'))}"
if old_test in source:
    source = source.replace(old_test, new_test, 1)

# Save budget/risk/Max Trades as soon as any of those values changes, while
# retaining the existing SAVE ALERTS button for Telegram credentials.
old_listener = "['budget','risk','maxtrades'].forEach(id=>$(id).addEventListener('input',()=>state()));"
new_listener = "['budget','risk','maxtrades'].forEach(id=>$(id).addEventListener('input',()=>{state();fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({budget:Number($('budget').value),risk:Number($('risk').value),max_trades:Number($('maxtrades').value)})})}));"
if old_listener in source:
    source = source.replace(old_listener, new_listener, 1)

exec(compile(source, path, 'exec'), {'__name__': '__main__', '__file__': path})
