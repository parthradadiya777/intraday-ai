import os
import threading
import time
import sitecustomize  # noqa: F401
import app_auto
from ai_engine import analyze as ai_analyze
from nsemine import live


def technical(symbol):
    cached = app_auto.STATE['technical_cache'].get(symbol)
    if cached and time.time() - cached['ts'] < app_auto.TECH_TTL:
        return cached['data']

    try:
        from nsemine import historical
        from datetime import datetime, timedelta
        end = datetime.now()
        start = end - timedelta(days=7)
        df = historical.get_stock_historical_data(symbol, start, end, interval=5)
        if df is not None and len(df) >= 40:
            data = ai_analyze(symbol, df)
            if data:
                app_auto.STATE['technical_cache'][symbol] = {'ts': time.time(), 'data': data}
                return data
    except Exception:
        pass

    try:
        df = live.get_stock_intraday_tick_by_tick_data(symbol, candle_interval=5)
        if df is not None and len(df) >= 40:
            data = ai_analyze(symbol, df)
            if data:
                app_auto.STATE['technical_cache'][symbol] = {'ts': time.time(), 'data': data}
                return data
    except Exception:
        pass

    # If 5-minute candles are unavailable, keep the server's live snapshot
    # result rather than inventing a second, conflicting signal formula.
    snap = getattr(app_auto, 'AI_SNAPSHOT', {}).get(symbol)
    if not snap:
        return None
    data = {
        'price': snap.get('price'),
        'momentum': snap.get('momentum'),
        'score': snap.get('score', 50.0),
        'signal': snap.get('signal', 'WAIT'),
        'target': snap.get('target'),
        'sl': snap.get('sl'),
        'ai_probability': snap.get('ai_probability'),
        'ai_confidence': snap.get('ai_confidence'),
        'ai_direction': snap.get('ai_direction'),
        'ai_model': 'SNAPSHOT-AI',
        'ai_validation': None,
        'ai_reason': 'Live NSE snapshot; 5-minute multi-factor data unavailable',
    }
    app_auto.STATE['technical_cache'][symbol] = {'ts': time.time(), 'data': data}
    return data


# server.py replaces the scanner with the complete-universe implementation.
app_auto.technical = technical

# Keep the clean UI available if this module is launched directly.
HTML = '''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Intraday AI</title></head><body><p>Intraday AI is running. Use server.py for the full scanner.</p></body></html>'''
app_auto.HTML = HTML

if __name__ == '__main__':
    threading.Thread(target=app_auto.auto_loop, daemon=True).start()
    port = int(os.environ.get('PORT', '10000'))
    app_auto.ThreadingHTTPServer(('0.0.0.0', port), app_auto.Handler).serve_forever()
