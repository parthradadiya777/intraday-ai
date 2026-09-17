import os
import threading
import time
import app_auto
import start  # installs the clean AI UI + AI technical engine


def scan():
    """Fresh NSE snapshot + rotating AI candidate groups; never pin recommendations to one pair."""
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

            app_auto.AI_SNAPSHOT = {x['symbol']: x for x in rows}
            ranked = sorted(rows, key=lambda x: (abs(x['change']), x['volume']), reverse=True)

            # Use 30 strong candidates and rotate through them every minute.
            pool = ranked[:30]
            groups = max(1, (len(pool) + 5) // 6)
            group = (int(time.time()) // 60) % groups
            candidates = pool[group * 6:(group + 1) * 6]
            if len(candidates) < 6:
                candidates = pool[:6]

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

            selected = {x['symbol'] for x in candidates}
            for x in rows:
                if x['symbol'] not in selected:
                    x['signal'] = 'WAIT'
                    x['score'] = 50.0
            rows.sort(key=lambda x: x['score'], reverse=True)
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

if __name__ == '__main__':
    threading.Thread(target=app_auto.auto_loop, daemon=True).start()
    port = int(os.environ.get('PORT', '10000'))
    app_auto.ThreadingHTTPServer(('0.0.0.0', port), app_auto.Handler).serve_forever()
