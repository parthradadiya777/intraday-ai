import os
import threading
import time
import urllib.parse
import math
from datetime import datetime
import requests
import app_auto
import start
from nsemine import live, historical

# NIFTY50_DIRECT_PANEL

app_auto.STATE.setdefault('progress', 0)
app_auto.STATE.setdefault('progress_text', 'Ready')
app_auto.STATE.setdefault('scan_id', 0)

def snapshot_ai(row, max_volume):
    price = float(row.get('price', 0) or 0)
    change = float(row.get('change', 0) or 0)
    volume = float(row.get('volume', 0) or 0)
    volume_boost = min(8.0, (volume / max(max_volume, 1.0)) * 8.0)
    score = max(0.0, min(100.0, 50.0 + change * 1.6 + volume_boost))
    confidence = max(50.0, min(78.0, 50.0 + abs(change) * 1.3 + volume_boost * 0.35))
    if change >= 0.75 and score >= 65.0:
        signal = 'BUY'
    elif change <= -0.75 and score <= 35.0:
        signal = 'SELL'
    else:
        signal = 'WAIT'
    target = price * (1.012 if signal == 'BUY' else 0.988) if signal in ('BUY', 'SELL') else None
    sl = price * (0.99 if signal == 'BUY' else 1.01) if signal in ('BUY', 'SELL') else None
    return {
        'score': round(score, 1), 'signal': signal,
        'ai_confidence': round(confidence, 1), 'ai_model': 'SNAPSHOT-AI',
        'ai_probability': round(score / 100.0, 4),
        'ai_direction': 'UP' if change >= 0 else 'DOWN',
        'target': round(target, 2) if target else None,
        'sl': round(sl, 2) if sl else None,
        'momentum': round(change, 3), 'ai_validation': None,
        'ai_reason': 'Live NSE snapshot; selected stocks get 5-minute AI refinement',
    }

def _nse_nifty_snapshot():
    """Fetch current NIFTY 50 bulk prices from NSE."""
    headers = {'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/153 Safari/537.36',
               'Accept':'application/json,text/plain,*/*',
               'Referer':'https://www.nseindia.com/market-data/live-equity-market?key=NIFTY+50'}
    sess = requests.Session(); sess.headers.update(headers)
    sess.get('https://www.nseindia.com', timeout=8)
    rr = sess.get('https://www.nseindia.com/api/equity-stockIndices', params={'index':'NIFTY 50'}, timeout=10)
    rr.raise_for_status(); raw = rr.json()
    data = raw.get('data',[]) if isinstance(raw,dict) else []
    out=[]
    for x in data:
        sym=str(x.get('symbol') or '').strip().upper()
        if not sym or sym in ('NIFTY 50','NIFTY50'): continue
        price=x.get('lastPrice',x.get('ltp')); ch=x.get('pChange',x.get('percentChange'))
        if price is None: continue
        out.append({'symbol':sym,'price':round(float(price),2),'change':round(float(ch or 0),2),
                    'volume':int(float(x.get('totalTradedVolume',x.get('volume') or 0) or 0)),
                    'score':50.0,'signal':'WAIT','rsi':None,'ema9':None,'ema21':None,'momentum':None,
                    'source':'NSE LIVE','ai_confidence':None,'ai_model':None,'target':None,'sl':None,
                    'ai_validation':None,'ai_reason':None})
    return out
def scan():
    if app_auto.STATE.get('rows') and time.time() - app_auto.STATE.get('ts', 0) < 5:
        return app_auto.STATE.get('rows', [])
    with app_auto.STATE['lock']:
        if app_auto.STATE.get('scanning'):
            return app_auto.STATE.get('rows', [])
        app_auto.STATE['scanning'] = True
        app_auto.STATE['progress'] = 2
        app_auto.STATE['progress_text'] = 'Fetching fresh NSE market data…'
        try:
            df = app_auto.get_snapshot().copy()
            df['symbol'] = df['symbol'].astype(str).str.strip()
            df = df.drop_duplicates('symbol')
            # Product scope: only NIFTY 50 constituents.
            nifty_set = set(NIFTY_SYMBOLS)
            df = df[df['symbol'].map(lambda s: _clean_nifty_symbol(s) in nifty_set)].copy()
            rows = []
            for _, r in df.iterrows():
                try:
                    rows.append({
                        'symbol': str(r['symbol']).strip(),
                        'price': round(float(r['close']), 2),
                        'change': round(float(r['changepct']), 2),
                        'volume': int(float(r['volume'])) if r.get('volume') is not None else 0,
                        'score': 50.0, 'signal': 'WAIT', 'rsi': None,
                        'ema9': None, 'ema21': None, 'momentum': None,
                        'source': 'NSE', 'ai_confidence': None, 'ai_model': None,
                        'target': None, 'sl': None, 'ai_validation': None,
                        'ai_reason': None,
                    })
                except Exception:
                    continue
            # NSE bulk snapshots can occasionally omit one constituent temporarily.
            # Fill any missing NIFTY 50 symbol individually so the UI always shows all 50.
            try:
                have = {_clean_nifty_symbol(x.get('symbol')) for x in rows}
                missing = [s for s in NIFTY_SYMBOLS if s not in have]
                for sym in missing:
                    try:
                        q = live.get_stock_live_quotes(sym)
                        if q and q.get('close') is not None:
                            rows.append({
                                'symbol': sym,
                                'price': round(float(q.get('close')), 2),
                                'change': round(float(q.get('changepct') or 0), 2),
                                'volume': int(float(q.get('volume') or 0)),
                                'score': 50.0, 'signal': 'WAIT', 'rsi': None,
                                'ema9': None, 'ema21': None, 'momentum': None,
                                'source': 'NSE LIVE', 'ai_confidence': None, 'ai_model': None,
                                'target': None, 'sl': None, 'ai_validation': None,
                                'ai_reason': 'Individual NSE live fallback for missing NIFTY 50 constituent',
                            })
                    except Exception:
                        pass
            except Exception:
                pass
            if not rows:
                try:
                    rows = _nse_nifty_snapshot()
                except Exception:
                    rows = []
            if not rows:
                return app_auto.STATE.get('rows', [])

            app_auto.STATE['progress'] = 20
            app_auto.STATE['progress_text'] = f'Scoring {len(rows)} NSE stocks…'
            max_volume = max((x['volume'] for x in rows), default=1)
            for x in rows:
                x.update(snapshot_ai(x, max_volume))
            app_auto.AI_SNAPSHOT = {x['symbol']: x for x in rows}

            candidates = sorted(
                rows,
                key=lambda x: (x['score'], abs(x['change']), x['volume']),
                reverse=True
            )[:15]
            app_auto.STATE['progress'] = 35
            app_auto.STATE['progress_text'] = f'Running 5-minute AI on {len(candidates)} strongest candidates…'

            from concurrent.futures import ThreadPoolExecutor, as_completed
            done = 0
            with ThreadPoolExecutor(max_workers=5) as ex:
                futures = {ex.submit(start.technical, x['symbol']): x for x in candidates}
                for future in as_completed(futures):
                    try:
                        data = future.result()
                        if data:
                            futures[future].update(data)
                    except Exception:
                        pass
                    done += 1
                    app_auto.STATE['progress'] = 35 + int(done / len(candidates) * 55)
                    app_auto.STATE['progress_text'] = f'AI refinement {done}/{len(candidates)}…'
            # Add option-chain context to the strongest F&O candidates.
            for x in candidates[:5]:
                try:
                    ob=option_bias(x['symbol'])
                    if ob:
                        x.update(ob)
                        x['score']=round(max(0.0,min(100.0,float(x.get('score',50))+float(ob.get('option_bias',0)))),1)
                        if x.get('signal')=='WAIT' and x['score']>=67:
                            x['signal']='BUY'
                        elif x.get('signal')=='WAIT' and x['score']<=33:
                            x['signal']='SELL'
                        x['ai_reason']=(x.get('ai_reason') or '')+' • Option-chain PCR included'
                except Exception:
                    pass

            rows.sort(key=lambda x: (x['score'], x['signal'] != 'WAIT', abs(x['change']), x['volume']), reverse=True)
            app_auto.STATE['rows'] = rows
            app_auto.STATE['ts'] = time.time()
            app_auto.STATE['last_error'] = ''
            app_auto.STATE['scan_id'] = int(app_auto.STATE.get('scan_id', 0)) + 1
            app_auto.STATE['progress'] = 100
            app_auto.STATE['progress_text'] = f'Complete • {len(rows)} NSE stocks scanned'
            return rows
        except Exception as e:
            app_auto.STATE['last_error'] = str(e)[:160]
            app_auto.STATE['progress_text'] = 'Scan failed — showing last available data'
            return app_auto.STATE.get('rows', [])
        finally:
            app_auto.STATE['scanning'] = False

app_auto.scan = scan

def start_background_scan(force=False):
    if app_auto.STATE.get('scanning'):
        return False
    if not force and app_auto.STATE.get('rows') and time.time() - app_auto.STATE.get('ts', 0) < 5:
        return False
    threading.Thread(target=scan, daemon=True).start()
    return True

app_auto.HTML = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<script src="https://unpkg.com/lightweight-charts@4.2.2/dist/lightweight-charts.standalone.production.js"></script>
<title>Intraday AI • NIFTY Live v5</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f4f6f8;color:#172033;font-family:Arial,sans-serif}