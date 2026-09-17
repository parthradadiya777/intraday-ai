import os
import re
import time

path = os.path.join(os.path.dirname(__file__), 'app_auto.py')
source = open(path, encoding='utf-8').read()
source = "from ai_engine import analyze as ai_analyze\n" + source
source = "AI_SNAPSHOT = {}\n" + source

# Use recent 5-minute history as the primary AI input. This avoids depending on the
# live intraday endpoint, which can be unavailable/rate-limited while NSE snapshot works.
new_technical = '''def technical(symbol):
    cached = STATE['technical_cache'].get(symbol)
    if cached and time.time() - cached['ts'] < TECH_TTL:
        return cached['data']

    # Primary: recent 5-minute historical candles -> ML/technical AI engine.
    try:
        from nsemine import historical
        from datetime import datetime, timedelta
        end = datetime.now()
        start = end - timedelta(days=7)
        df = historical.get_stock_historical_data(symbol, start, end, interval=5)
        if df is not None and len(df) >= 30:
            data = ai_analyze(symbol, df)
            if data:
                STATE['technical_cache'][symbol] = {'ts': time.time(), 'data': data}
                return data
    except Exception as e:
        STATE['last_error'] = 'AI 5m history: ' + str(e)[:140]

    # Secondary: live 5-minute endpoint if historical data is unavailable.
    try:
        df = live.get_stock_intraday_tick_by_tick_data(symbol, candle_interval=5)
        if df is not None and len(df) >= 30:
            data = ai_analyze(symbol, df)
            if data:
                STATE['technical_cache'][symbol] = {'ts': time.time(), 'data': data}
                return data
    except Exception as e:
        STATE['last_error'] = 'AI 5m live: ' + str(e)[:140]

    # Last resort: live NSE snapshot. Explicitly labelled; never pretend this is ML.
    x = AI_SNAPSHOT.get(symbol)
    if not x:
        return None
    ch = float(x.get('change', 0) or 0)
    price = float(x.get('price', 0) or 0)
    vol = float(x.get('volume', 0) or 0)
    max_vol = max(float(max((v.get('volume', 0) or 0) for v in AI_SNAPSHOT.values())), 1)
    vol_boost = min(8.0, (vol / max_vol) * 8.0)
    # Snapshot score is intentionally conservative because it is not a trained model.
    score = max(0.0, min(100.0, 50.0 + ch * 1.6 + vol_boost))
    confidence = max(50.0, min(78.0, 50.0 + abs(ch) * 1.3 + vol_boost * 0.35))
    if ch >= 5.0 and score >= 68.0:
        signal = 'BUY'
    elif ch <= -5.0 and score <= 32.0:
        signal = 'SELL'
    else:
        signal = 'WAIT'
    target = price * (1.012 if signal != 'SELL' else 0.988)
    sl = price * (0.99 if signal != 'SELL' else 1.01)
    data = {
        'price': round(price, 2), 'rsi': None, 'ema9': None, 'ema21': None,
        'momentum': round(ch, 3), 'score': round(score, 1), 'signal': signal,
        'target': round(target, 2), 'sl': round(sl, 2),
        'ai_probability': round(score / 100.0, 4), 'ai_confidence': round(confidence, 1),
        'ai_direction': 'UP' if ch >= 0 else 'DOWN', 'ai_model': 'AI-FALLBACK',
        'ai_validation': None,
        'ai_reason': 'live NSE snapshot fallback; 5-minute history unavailable',
    }
    STATE['technical_cache'][symbol] = {'ts': time.time(), 'data': data}
    return data
'''
source = re.sub(r"def technical\(symbol\):.*?\n\ndef get_snapshot", new_technical + "\n\ndef get_snapshot", source, count=1, flags=re.S)

# Replace the complete scan so AI values are always merged into candidate rows.
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
                                 'ai_validation': None, 'ai_reason': None})
                except Exception:
                    continue
            if not rows:
                raise RuntimeError('NSE live market data had no usable equity rows')

            AI_SNAPSHOT.clear()
            AI_SNAPSHOT.update({x['symbol']: x for x in rows})
            # Limit expensive model training to the strongest six candidates.
            candidates = sorted(rows, key=lambda x: (abs(x['change']), x['volume']), reverse=True)[:6]
            with ThreadPoolExecutor(max_workers=3) as ex:
                futures = {ex.submit(technical, x['symbol']): x for x in candidates}
                for f in as_completed(futures):
                    x = futures[f]
                    try:
                        ai = f.result()
                        if ai:
                            x.update(ai)
                    except Exception:
                        pass

            # Non-candidates remain WAIT with no fake AI confidence/model.
            candidate_symbols = {c['symbol'] for c in candidates}
            for x in rows:
                if x['symbol'] not in candidate_symbols:
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

source = source.replace('NSE equity scanner • automatic monitoring • BUY / WAIT / SELL • position alerts',
                        'NSE equity scanner • AI prediction • BUY / WAIT / SELL • target & stop monitoring', 1)
source = source.replace('<th>Score</th><th>Signal</th>',
                        '<th>AI Score</th><th>AI Confidence</th><th>Model</th><th>Signal</th>', 1)
source = source.replace("<td>'+x.score+'</td><td><span class=\"tag\">'+x.signal+'</span></td>",
                        "<td>'+x.score+'</td><td>'+fmt(x.ai_confidence)+'%</td><td>'+fmt(x.ai_model)+'</td><td><span class=\"tag\">'+x.signal+'</span></td>", 1)
source = source.replace("money(x.price*1.015)", "money(x.target||x.price*1.015)", 1)
source = source.replace("money(x.price*.99)", "money(x.sl||x.price*.99)", 1)
source = source.replace('Automatic system scans NSE with a low-frequency refresh to reduce rate limits. Strong BUY/SELL alerts are sent once per stock.',
                        'AI scans NSE candidates using recent 5-minute history when available, with an explicitly marked live-snapshot fallback. Signals are probabilistic, not guaranteed.', 1)

# Critical budget/risk fix: quantities must never exceed the entered investment budget.
old_qty = "let budget=Number($('budget').value||5000),risk=Number($('risk').value||500);$('rows').innerHTML=rows.slice(0,300).map(x=>{let b=x.price>0?Math.floor(budget/x.price):0;let rq=x.price>0?Math.floor(risk/(x.price*.01)):0;let q=Math.max(0,Math.min(b,rq||b));return"
new_qty = "let budget=Math.max(0,Number($('budget').value||5000)),risk=Math.max(0,Number($('risk').value||500));$('rows').innerHTML=rows.slice(0,300).map(x=>{let b=x.price>0?Math.floor(budget/x.price):0;let slGap=x.price>0&&x.sl!=null?Math.abs(x.price-x.sl):x.price*.01;let rq=slGap>0?Math.floor(risk/slGap):0;let q=Math.max(0,Math.min(b,rq||b));return"
source = source.replace(old_qty, new_qty, 1)
source = source.replace("<td>'+x.score+'</td><td><span", "<td>'+x.score+'</td><td><span", 1)
# Add explicit investment column after Plan Qty when the original table is used.
source = source.replace('<th>Plan Qty</th><th>Target</th>', '<th>Plan Qty</th><th>Investment</th><th>Target</th>', 1)
source = source.replace("<td>'+q+'</td><td>'+money(x.target||x.price*1.015)+'</td>", "<td>'+q+'</td><td>'+money(q*x.price)+'</td><td>'+money(x.target||x.price*1.015)+'</td>", 1)
source = source.replace('Automatic system scans NSE with a low-frequency refresh to reduce rate limits. Strong BUY/SELL alerts are sent once per stock.',
                        'AI scans NSE candidates using recent 5-minute history when available, with an explicitly marked live-snapshot fallback. Signals are probabilistic, not guaranteed.', 1)

exec(compile(source, path, 'exec'), {'__name__': '__main__', '__file__': path})
