import os
import re
import time

path = os.path.join(os.path.dirname(__file__), 'app_auto.py')
source = open(path, encoding='utf-8').read()
source = "from ai_engine import analyze as ai_analyze\n" + source
source = "AI_SNAPSHOT = {}\n" + source

# Replace the original technical function with a real 5-minute AI analysis.
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

    # Live NSE snapshot fallback. Explicitly labelled; never pretend this is ML.
    x = AI_SNAPSHOT.get(symbol)
    if not x:
        return None
    ch = float(x.get('change', 0) or 0)
    price = float(x.get('price', 0) or 0)
    vol = float(x.get('volume', 0) or 0)
    max_vol = max(float(max((v.get('volume', 0) or 0) for v in AI_SNAPSHOT.values())), 1)
    vol_boost = min(10.0, (vol / max_vol) * 10.0)
    score = max(0.0, min(100.0, 50.0 + ch * 2.2 + vol_boost))
    confidence = max(50.0, min(82.0, 50.0 + abs(ch) * 2.0 + vol_boost * 0.4))
    if ch >= 4.0 and score >= 65.0:
        signal = 'BUY'
    elif ch <= -4.0 and score <= 35.0:
        signal = 'SELL'
    else:
        signal = 'WAIT'
    target = price * (1.015 if signal != 'SELL' else 0.985)
    sl = price * (0.99 if signal != 'SELL' else 1.01)
    data = {
        'price': round(price, 2), 'rsi': None, 'ema9': None, 'ema21': None,
        'momentum': round(ch, 3), 'score': round(score, 1), 'signal': signal,
        'target': round(target, 2), 'sl': round(sl, 2),
        'ai_probability': round(score / 100.0, 4), 'ai_confidence': round(confidence, 1),
        'ai_direction': 'UP' if ch >= 0 else 'DOWN', 'ai_model': 'AI-FALLBACK',
        'ai_validation': None,
        'ai_reason': 'live NSE snapshot fallback; 5-minute candle feed unavailable',
    }
    STATE['technical_cache'][symbol] = {'ts': time.time(), 'data': data}
    return data
'''
source = re.sub(r"def technical\(symbol\):.*?\n\ndef get_snapshot", new_technical + "\n\ndef get_snapshot", source, count=1, flags=re.S)

# Replace scan completely so AI values are always merged into the displayed candidate rows.
new_scan = '''def scan():
    if STATE['rows'] and time.time() - STATE['ts'] < SCAN_TTL:
        return STATE['rows']
    with STATE['lock']:
        if STATE['rows'] and time.time() - STATE['ts'] < SCAN_TTL:
            return STATE['rows']
        STATE['scanning'] = True
        try:
            live_df = get_snapshot().copy()
            live_df['symbol'] = live_df['symbol'].astype(str).str.strip()
            live_df = live_df.drop_duplicates('symbol')
            rows = []
            for _, r in live_df.iterrows():
                try:
                    sym = str(r['symbol']).strip()
                    price = float(r['close'])
                    change = float(r['changepct'])
                    volume = int(float(r['volume'])) if r.get('volume') is not None else 0
                    rows.append({'symbol': sym, 'price': round(price, 2), 'change': round(change, 2),
                                 'volume': volume, 'score': 50.0, 'signal': 'WAIT', 'rsi': None,
                                 'ema9': None, 'ema21': None, 'momentum': None, 'source': 'NSE',
                                 'ai_confidence': None, 'ai_model': None, 'target': None, 'sl': None,
                                 'ai_reason': None})
                except Exception:
                    continue
            if not rows:
                raise RuntimeError('NSE live market data had no usable equity rows')

            AI_SNAPSHOT.clear()
            AI_SNAPSHOT.update({x['symbol']: x for x in rows})
            candidates = sorted(rows, key=lambda x: (abs(x['change']), x['volume']), reverse=True)[:12]
            with ThreadPoolExecutor(max_workers=4) as ex:
                futures = {ex.submit(technical, x['symbol']): x for x in candidates}
                for f in as_completed(futures):
                    x = futures[f]
                    try:
                        ai = f.result()
                        if ai:
                            x.update(ai)
                    except Exception:
                        pass

            # Keep non-candidates visible but never give them a fake AI signal.
            for x in rows:
                if x['symbol'] not in {c['symbol'] for c in candidates}:
                    x['signal'] = 'WAIT'
            rows.sort(key=lambda x: x['score'], reverse=True)
            STATE['rows'], STATE['ts'], STATE['last_error'] = rows, time.time(), ''
            return rows
        except Exception as e:
            STATE['last_error'] = str(e)[:160]
            if STATE['rows']:
                return STATE['rows']
            raise
        finally:
            STATE['scanning'] = False
'''
source = re.sub(r"def scan\(\):.*?\n\ndef alert_once", new_scan + "\n\ndef alert_once", source, count=1, flags=re.S)

# Make the page visibly identify the AI fields. Use broad substitutions so small source changes cannot hide them.
source = source.replace('NSE equity scanner • automatic monitoring • BUY / WAIT / SELL • position alerts',
                        'NSE equity scanner • AI prediction • BUY / WAIT / SELL • target & stop monitoring', 1)
source = source.replace('<th>Score</th><th>Signal</th>',
                        '<th>AI Score</th><th>AI Confidence</th><th>Model</th><th>Signal</th>', 1)
source = source.replace("<td>'+x.score+'</td><td><span class=\"tag\">'+x.signal+'</span></td>",
                        "<td>'+x.score+'</td><td>'+fmt(x.ai_confidence)+'%</td><td>'+fmt(x.ai_model)+'</td><td><span class=\"tag\">'+x.signal+'</span></td>", 1)
source = source.replace("money(x.price*1.015)", "money(x.target||x.price*1.015)", 1)
source = source.replace("money(x.price*.99)", "money(x.sl||x.price*.99)", 1)
source = source.replace('Automatic system scans NSE with a low-frequency refresh to reduce rate limits. Strong BUY/SELL alerts are sent once per stock.',
                        'AI scans NSE candidates using 5-minute data when available and an explicitly marked live-snapshot fallback. Signals are probabilistic, not guaranteed.', 1)

exec(compile(source, path, 'exec'), {'__name__': '__main__', '__file__': path})
