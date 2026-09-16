# Cloud market-data fallback for Intraday AI.
# IMPORTANT: Telegram/bot code is intentionally not touched here.
# If one market-data source fails, try the next source instead of failing the scan.
import json, urllib.request, urllib.parse

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
            headers={
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'User-Agent': 'Mozilla/5.0',
                'Referer': 'https://www.tradingview.com/'
            },
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=18) as r:
            obj = json.loads(r.read().decode('utf-8'))
        import pandas as pd
        rows = []
        for item in obj.get('data') or []:
            symbol = str(item.get('s', '')).strip()
            values = item.get('d') or []
            if ':' not in symbol or len(values) < 4:
                continue
            exchange, sym = symbol.split(':', 1)
            if exchange.upper() != 'NSE':
                continue
            try:
                price = float(values[1])
                change = float(values[2] or 0)
                volume = int(float(values[3] or 0))
            except Exception:
                continue
            if price > 0:
                rows.append({
                    'symbol': sym,
                    'series': 'EQ',
                    'close': price,
                    'changepct': change,
                    'volume': volume
                })
        if not rows:
            raise RuntimeError('TradingView returned no NSE rows')
        return pd.DataFrame(rows)

    def _api_snapshot():
        # Secondary public fallback. This API is Yahoo-backed and does not need
        # a token. It is intentionally a small safety net, not the primary source.
        base = 'http://65.0.104.9'
        symbols_url = base + '/symbols'
        req = urllib.request.Request(symbols_url, headers={'User-Agent': 'IntradayAI/3'})
        with urllib.request.urlopen(req, timeout=10) as r:
            obj = json.loads(r.read().decode('utf-8'))
        symbols = []
        for x in obj.get('symbols') or []:
            s = str(x.get('symbol') or '').strip().upper()
            if s:
                symbols.append(s)
        symbols = list(dict.fromkeys(symbols))[:30]
        if not symbols:
            raise RuntimeError('Secondary market API returned no symbols')
        q = urllib.parse.quote(','.join(symbols), safe=',')
        url = base + '/stock/list?symbols=' + q + '&res=num'
        req = urllib.request.Request(url, headers={'User-Agent': 'IntradayAI/3'})
        with urllib.request.urlopen(req, timeout=15) as r:
            obj = json.loads(r.read().decode('utf-8'))
        import pandas as pd
        rows = []
        for x in obj.get('stocks') or []:
            try:
                sym = str(x.get('symbol') or '').strip().upper()
                price = float(x.get('last_price'))
                change = float(x.get('percent_change') or 0)
                volume = int(float(x.get('volume') or 0))
                if sym and price > 0:
                    rows.append({'symbol': sym, 'series': 'EQ', 'close': price,
                                 'changepct': change, 'volume': volume})
            except Exception:
                continue
        if not rows:
            raise RuntimeError('Secondary market API returned no usable rows')
        return pd.DataFrame(rows)

    def _snapshot(*args, **kwargs):
        errors = []
        try:
            df = _original_snapshot(*args, **kwargs)
            if df is not None and len(df) > 0:
                return df
            errors.append('NSE empty')
        except Exception as e:
            errors.append('NSE: ' + str(e)[:80])
        try:
            return _tradingview_snapshot()
        except Exception as e:
            errors.append('TradingView: ' + str(e)[:80])
        try:
            return _api_snapshot()
        except Exception as e:
            errors.append('Fallback API: ' + str(e)[:80])
        raise RuntimeError(' | '.join(errors))

    _live.get_all_securities_live_snapshot = _snapshot
except Exception:
    pass
