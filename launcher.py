import os

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

# Make Telegram errors visible to TEST PHONE without exposing the token.
old_tg_error = '''    except Exception:
        return False


def ema'''
new_tg_error = '''    except Exception as e:
        STATE['last_error'] = 'Telegram error: ' + str(e)[:180]
        return False


def ema'''
if old_tg_error in source:
    source = source.replace(old_tg_error, new_tg_error, 1)

source = source.replace(
    "ok=telegram('✅ Intraday AI test alert — Telegram is connected.'); self.send_json({'ok':ok}); return",
    "ok=telegram('✅ Intraday AI test alert — Telegram is connected.'); self.send_json({'ok':ok,'error': '' if ok else STATE.get('last_error','Telegram send failed')}); return",
    1,
)

# Budget/risk/Max Trades update immediately and persist as the user types.
needle = "setInterval(state,15000);state();"
replacement = "['budget','risk','maxtrades'].forEach(id=>$(id).addEventListener('input',()=>{state();fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({budget:Number($('budget').value),risk:Number($('risk').value),max_trades:Number($('maxtrades').value)})})}));setInterval(state,15000);state();"
if needle in source:
    source = source.replace(needle, replacement, 1)

old_test = "async function testAlert(){let r=await fetch('/api/test');let j=await r.json();$('msg').textContent=j.ok?'Test sent':'Test failed — check Telegram token/chat ID'}"
new_test = "async function testAlert(){let r=await fetch('/api/test');let j=await r.json();$('msg').textContent=j.ok?'Test sent ✅':('Telegram failed: '+(j.error||'check Bot Token and Chat ID'))}"
if old_test in source:
    source = source.replace(old_test, new_test, 1)

# Inject the ML decision engine into the app process.
source = "from ai_engine import analyze as ai_analyze\n" + source

# Replace the old rule-only technical scorer with the ML engine.
new_technical = '''def technical(symbol):
    cached = STATE['technical_cache'].get(symbol)
    if cached and time.time() - cached['ts'] < TECH_TTL:
        return cached['data']
    try:
        df = live.get_stock_intraday_tick_by_tick_data(symbol, candle_interval=5)
        if df is None or len(df) < 30:
            return None
        data = ai_analyze(symbol, df)
        if not data:
            return None
        STATE['technical_cache'][symbol] = {'ts': time.time(), 'data': data}
        return data
    except Exception as e:
        STATE['last_error'] = 'AI analysis: ' + str(e)[:140]
        return None
'''
source = re.sub(r"def technical\(symbol\):.*?\n\ndef get_snapshot", new_technical + "\n\ndef get_snapshot", source, count=1, flags=re.S)

# Do not create BUY/SELL from daily change alone. AI must confirm them.
source = source.replace(
    "x['signal'] = 'BUY' if ch >= 2 else 'SELL' if ch <= -2 else 'WAIT'",
    "x['signal'] = 'WAIT'",
    1,
)
source = source.replace("[:10]", "[:12]", 1)

# Use AI-derived target/stop and explain the signal in alerts.
source = source.replace(
    "alert_once('buy_' + x['symbol'], f\"🟢 BUY NOW\\n{x['symbol']}\\nPrice ₹{x['price']}\\nScore {x['score']}\\nTarget ₹{x['price']*1.015:.2f}\\nStop Loss ₹{x['price']*.99:.2f}\")",
    "alert_once('buy_' + x['symbol'], f\"🟢 AI BUY NOW\\n{x['symbol']}\\nPrice ₹{x['price']}\\nAI Confidence {x.get('ai_confidence','—')}%\\nTarget ₹{x.get('target',x['price']*1.015):.2f}\\nStop Loss ₹{x.get('sl',x['price']*.99):.2f}\\nModel {x.get('ai_model','AI')}\\nReason {x.get('ai_reason','')}\")",
    1,
)
source = source.replace(
    "alert_once('short_' + x['symbol'], f\"🔴 SELL SETUP\\n{x['symbol']}\\nPrice ₹{x['price']}\\nScore {x['score']}\\nTarget ₹{x['price']*.985:.2f}\\nStop Loss ₹{x['price']*1.01:.2f}\")",
    "alert_once('short_' + x['symbol'], f\"🔴 AI SELL / SHORT SETUP\\n{x['symbol']}\\nPrice ₹{x['price']}\\nAI Confidence {x.get('ai_confidence','—')}%\\nTarget ₹{x.get('target',x['price']*.985):.2f}\\nStop Loss ₹{x.get('sl',x['price']*1.01):.2f}\\nModel {x.get('ai_model','AI')}\\nReason {x.get('ai_reason','')}\")",
    1,
)

source = source.replace(
    "<div class=\"sub\">NSE equity scanner • automatic monitoring • BUY / WAIT / SELL • position alerts</div>",
    "<div class=\"sub\">NSE equity scanner • ML prediction • BUY / WAIT / SELL • target & stop monitoring</div>",
    1,
)
source = source.replace(
    "<th>Score</th><th>Signal</th>",
    "<th>AI Score</th><th>AI Confidence</th><th>Model</th><th>Signal</th>",
    1,
)
source = source.replace(
    "<td>'+x.score+'</td><td><span class=\"tag\">'+x.signal+'</span></td>",
    "<td>'+x.score+'</td><td>'+fmt(x.ai_confidence)+'%</td><td>'+fmt(x.ai_model)+'</td><td><span class=\"tag\">'+x.signal+'</span></td>",
    1,
)
source = source.replace("money(x.price*1.015)", "money(x.target||x.price*1.015)", 1)
source = source.replace("money(x.price*.99)", "money(x.sl||x.price*.99)", 1)
source = source.replace(
    "Automatic system scans NSE with a low-frequency refresh to reduce rate limits. Strong BUY/SELL alerts are sent once per stock.",
    "AI system scans NSE, trains on recent 5-minute history for the strongest candidates, then confirms BUY/SELL only when the learned probability and live trend agree. Signals are probabilistic, not guaranteed.",
    1,
)

exec(compile(source, path, 'exec'), {'__name__': '__main__', '__file__': path})
