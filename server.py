import os
import threading
import time
import app_auto
import start  # clean AI UI + technical engine


def snapshot_ai(row, max_volume):
    """Fast first-pass scoring for the complete NSE universe."""
    price = float(row.get('price', 0) or 0)
    change = float(row.get('change', 0) or 0)
    volume = float(row.get('volume', 0) or 0)
    volume_boost = min(8.0, (volume / max(max_volume, 1.0)) * 8.0)
    score = max(0.0, min(100.0, 50.0 + change * 1.6 + volume_boost))
    confidence = max(50.0, min(78.0, 50.0 + abs(change) * 1.3 + volume_boost * 0.35))
    if change >= 5.0 and score >= 68.0:
        signal = 'BUY'
    elif change <= -5.0 and score <= 32.0:
        signal = 'SELL'
    else:
        signal = 'WAIT'
    target = price * (1.012 if signal == 'BUY' else 0.988) if signal in ('BUY', 'SELL') else None
    sl = price * (0.99 if signal == 'BUY' else 1.01) if signal in ('BUY', 'SELL') else None
    return {
        'score': round(score, 1), 'signal': signal,
        'ai_confidence': round(confidence, 1), 'ai_model': 'AI-FALLBACK',
        'ai_probability': round(score / 100.0, 4),
        'ai_direction': 'UP' if change >= 0 else 'DOWN',
        'target': round(target, 2) if target else None,
        'sl': round(sl, 2) if sl else None, 'momentum': round(change, 3),
        'ai_validation': None,
        'ai_reason': 'Live NSE snapshot first-pass; 5-minute ML refinement applied to selected candidates',
    }


def scan():
    """Evaluate the complete NSE universe, then refine strongest candidates with 5-minute AI."""
    with app_auto.STATE['lock']:
        app_auto.STATE['scanning'] = True
        try:
            df = app_auto.get_snapshot().copy()
            df['symbol'] = df['symbol'].astype(str).str.strip()
            df = df.drop_duplicates('symbol')
            rows = []
            for _, r in df.iterrows():
                try:
                    rows.append({
                        'symbol': str(r['symbol']).strip(),
                        'price': round(float(r['close']), 2),
                        'change': round(float(r['changepct']), 2),
                        'volume': int(float(r['volume'])) if r.get('volume') is not None else 0,
                        'score': 50.0, 'signal': 'WAIT', 'rsi': None, 'ema9': None,
                        'ema21': None, 'momentum': None, 'source': 'NSE',
                        'ai_confidence': None, 'ai_model': None, 'target': None, 'sl': None,
                        'ai_validation': None, 'ai_reason': None
                    })
                except Exception:
                    continue
            if not rows:
                raise RuntimeError('NSE returned no usable equity rows')

            # Pass 1: ALL NSE stocks receive a live-snapshot AI score.
            max_volume = max((x['volume'] for x in rows), default=1)
            for x in rows:
                x.update(snapshot_ai(x, max_volume))
            app_auto.AI_SNAPSHOT = {x['symbol']: x for x in rows}

            # Pass 2: historical/live 5-minute AI refines the strongest 30 from the complete universe.
            candidates = sorted(rows, key=lambda x: (x['score'], abs(x['change']), x['volume']), reverse=True)[:30]
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=3) as ex:
                futures = {ex.submit(start.technical, x['symbol']): x for x in candidates}
                for future in as_completed(futures):
                    try:
                        data = future.result()
                        if data:
                            futures[future].update(data)
                    except Exception:
                        pass

            rows.sort(key=lambda x: (x['score'], abs(x['change']), x['volume']), reverse=True)
            app_auto.STATE['rows'] = rows
            app_auto.STATE['ts'] = time.time()
            app_auto.STATE['last_error'] = ''
            return rows
        except Exception as e:
            app_auto.STATE['last_error'] = str(e)[:160]
            return app_auto.STATE.get('rows', [])
        finally:
            app_auto.STATE['scanning'] = False


app_auto.scan = scan

# Make the existing clean UI comfortable on phones without changing desktop layout.
try:
    _mobile = '''<style>
@media (max-width: 700px){
 body{font-size:14px;overflow-x:hidden}
 header{padding:14px 12px;position:sticky;top:0;z-index:10}
 h1{font-size:22px}.sub{font-size:12px}.status{margin-top:8px;gap:6px}.chip{padding:6px 8px;font-size:11px}
 .wrap{padding:8px}.panel{padding:13px;border-radius:12px;margin-bottom:10px}
 .controls{display:grid;grid-template-columns:1fr 1fr;gap:8px}.controls label{grid-column:1/-1;font-size:14px}
 .controls input,.controls button{width:100%;min-height:44px}.controls button{grid-column:1/-1}
 .hint{font-size:11px;line-height:1.4}
 h2{font-size:19px;margin:4px 0 12px}
 .recommend{grid-template-columns:1fr;gap:8px}.rec{padding:12px}.symbol{font-size:18px}.price{font-size:16px}.meta{font-size:12px}
 table{min-width:850px}th,td{padding:9px 7px;font-size:12px}
 .small{font-size:11px}
}
</style>'''
    app_auto.HTML = app_auto.HTML.replace('</head>', _mobile + '</head>')
except Exception:
    pass

if __name__ == '__main__':
    threading.Thread(target=app_auto.auto_loop, daemon=True).start()
    port = int(os.environ.get('PORT', '10000'))
    app_auto.ThreadingHTTPServer(('0.0.0.0', port), app_auto.Handler).serve_forever()
