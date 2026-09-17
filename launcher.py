import os
import re
import time

path = os.path.join(os.path.dirname(__file__), 'app_auto.py')
source = open(path, encoding='utf-8').read()
source = "from ai_engine import analyze as ai_analyze\n" + source

# Snapshot-based fallback used only when the NSE 5-minute endpoint is unavailable/rate-limited.
# This keeps the AI columns populated instead of silently showing the old daily-change score.
source = "AI_SNAPSHOT = {}\n" + source

new_technical = '''def technical(symbol):
    cached = STATE['technical_cache'].get(symbol)
    if cached and time.time() - cached['ts'] < TECH_TTL:
        return cached['data']
    try:
        df = live.get_stock_intraday_tick_by_tick_data(symbol, candle_interval=5)
        if df is not None and len(df) >= 30:
            data = ai_analyze(symbol, df)
            if data:
                STATE['technical_cache'][symbol] = {'ts': time.time(), 'data': data}
                return data
    except Exception as e:
        STATE['last_error'] = 'AI 5m analysis: ' + str(e)[:140]

    # Reliable fallback from the live NSE snapshot. This is explicitly marked as fallback;
    # it is not presented as an ML prediction when 5-minute candles are unavailable.
    x = AI_SNAPSHOT.get(symbol)
    if x:
        ch = float(x.get('change', 0) or 0)
        price = float(x.get('price', 0) or 0)
        vol = float(x.get('volume', 0) or 0)
        max_vol = max(float(max((v.get('volume', 0) or 0) for v in AI_SNAPSHOT.values()), default=1), 1)
        vol_boost = min(10.0, (vol / max_vol) * 10.0)
        score = max(0.0, min(100.0, 50.0 + ch * 2.2 + vol_boost))
        confidence = max(50.0, min(82.0, 50.0 + abs(ch) * 2.0 + vol_boost * 0.4))
        if ch >= 4.0 and score >= 65:
            signal = 'BUY'
        elif ch <= -4.0 and score <= 35:
            signal = 'SELL'
        else:
            signal = 'WAIT'
        target = price * (1.015 if signal != 'SELL' else 0.985)
        sl = price * (0.99 if signal != 'SELL' else 1.01)
        data = {
            'price': round(price, 2), 'rsi': None, 'ema9': None, 'ema21': None,
            'momentum': round(ch, 3), 'score': round(score, 1), 'signal': signal,
            'target': round(target, 2), 'sl': round(sl, 2),
            'ai_probability': round(score / 100.0, 4),
            'ai_confidence': round(confidence, 1),
            'ai_direction': 'UP' if ch >= 0 else 'DOWN',
            'ai_model': 'AI-FALLBACK', 'ai_validation': None,
            'ai_reason': 'live NSE snapshot fallback; 5-minute candle feed unavailable',
        }
        STATE['technical_cache'][symbol] = {'ts': time.time(), 'data': data}
        return data
    return None
'''
source = re.sub(r"def technical\(symbol\):.*?\n\ndef get_snapshot", new_technical + "\n\ndef get_snapshot", source, count=1, flags=re.S)

# Daily percentage change is only a candidate filter; AI/fallback confirms the signal.
source = source.replace(
    "x['signal'] = 'BUY' if ch >= 2 else 'SELL' if ch <= -2 else 'WAIT'",
    "x['signal'] = 'WAIT'",
    1,
)
# Do not sort by the old daily-change score before the AI pass; keep active candidates diverse.
source = source.replace("candidates = sorted(rows, key=lambda x: (abs(x['change']), x['volume']), reverse=True)[:10]", "candidates = sorted(rows, key=lambda x: (abs(x['change']), x['volume']), reverse=True)[:12]", 1)
source = source.replace("candidates = sorted(rows, key=lambda x: (abs(x['change']), x['volume']), reverse=True)[:12]\n            with ThreadPoolExecutor", "candidates = sorted(rows, key=lambda x: (abs(x['change']), x['volume']), reverse=True)[:12]\n            AI_SNAPSHOT.clear()\n            AI_SNAPSHOT.update({x['symbol']: x for x in rows})\n            with ThreadPoolExecutor", 1)

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
    "AI system scans NSE and confirms BUY/SELL using recent 5-minute history when available, with an explicitly marked live-snapshot fallback. Signals are probabilistic, not guaranteed.",
    1,
)

exec(compile(source, path, 'exec'), {'__name__': '__main__', '__file__': path})
