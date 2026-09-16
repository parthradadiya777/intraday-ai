# Cloud-safe market-data fallback loaded automatically by Python on Render.
import json, urllib.parse, urllib.request
try:
    from nsemine import live as _live
    _original_snapshot = _live.get_all_securities_live_snapshot
    def _yahoo_snapshot():
        base='https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved'
        merged={}
        for screen in ('most_actives','day_gainers','day_losers'):
            q=urllib.parse.urlencode({'formatted':'false','lang':'en-US','region':'IN','scrIds':screen,'count':250})
            req=urllib.request.Request(base+'?'+q,headers={'User-Agent':'Mozilla/5.0','Accept':'application/json'})
            with urllib.request.urlopen(req,timeout=10) as r: obj=json.loads(r.read().decode('utf-8'))
            result=(obj.get('finance') or {}).get('result') or []
            for x in ((result[0].get('quotes') or []) if result else []):
                sym=str(x.get('symbol','')).strip(); p=x.get('regularMarketPrice'); ch=x.get('regularMarketChangePercent')
                if sym.endswith('.NS') and p is not None and ch is not None:
                    merged[sym[:-3]]={'symbol':sym[:-3],'series':'EQ','close':float(p),'changepct':float(ch),'volume':int(x.get('regularMarketVolume') or 0)}
        if not merged: raise RuntimeError('Yahoo fallback returned no NSE rows')
        import pandas as pd
        return pd.DataFrame(list(merged.values()))
    def _snapshot(*args,**kwargs):
        try: return _original_snapshot(*args,**kwargs)
        except Exception: return _yahoo_snapshot()
    _live.get_all_securities_live_snapshot=_snapshot
except Exception:
    pass
