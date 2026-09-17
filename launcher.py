import os
import re

path = os.path.join(os.path.dirname(__file__), 'app_auto.py')
source = open(path, encoding='utf-8').read()
source = "from ai_engine import analyze as ai_analyze\n" + source

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

# Daily percentage change is only a candidate filter; AI confirms the signal.
source = source.replace(
    "x['signal'] = 'BUY' if ch >= 2 else 'SELL' if ch <= -2 else 'WAIT'",
    "x['signal'] = 'WAIT'",
    1,
)
source = source.replace("[:10]", "[:12]", 1)

# Expose the AI engine in the live UI.
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
    "AI system scans NSE and confirms BUY/SELL using recent 5-minute history plus live trend. Signals are probabilistic, not guaranteed.",
    1,
)

exec(compile(source, path, 'exec'), {'__name__': '__main__', '__file__': path})
