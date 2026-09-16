# Explicit cloud fallback for Intraday AI. Loaded before app_auto by Render.
import json, urllib.request

try:
    from nsemine import live as _live
    _original_snapshot = _live.get_all_securities_live_snapshot

    def _tradingview_snapshot():
        url = 'https://scanner.tradingview.com/india/scan'
        payload = {
            'filter': [],
            'options': {'lang': 'en'},
            'columns': ['name', 'close', 'change', 'volume'],
            'range': [0, 5000],
            'sort': {'sortBy': 'volume', 'sortOrder': 'desc', 'nullsFirst': False}
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type':'application/json','Accept':'application/json',
                     'User-Agent':'Mozilla/5.0','Referer':'https://www.tradingview.com/'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            obj = json.loads(r.read().decode('utf-8'))
        import pandas as pd
        rows=[]
        for item in obj.get('data') or []:
            symbol=str(item.get('s','')).strip(); values=item.get('d') or []
            if ':' not in symbol or len(values)<4: continue
            exchange,sym=symbol.split(':',1)
            if exchange.upper()!='NSE': continue
            try:
                price=float(values[1]); change=float(values[2] or 0); volume=int(float(values[3] or 0))
            except Exception: continue
            if price>0:
                rows.append({'symbol':sym,'series':'EQ','close':price,'changepct':change,'volume':volume})
        if not rows: raise RuntimeError('TradingView returned no NSE rows')
        return pd.DataFrame(rows)

    def _snapshot(*args, **kwargs):
        try:
            df=_original_snapshot(*args, **kwargs)
            if df is not None and len(df)>0: return df
        except Exception:
            pass
        return _tradingview_snapshot()

    _live.get_all_securities_live_snapshot=_snapshot
except Exception:
    pass
