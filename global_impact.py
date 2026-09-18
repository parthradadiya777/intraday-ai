import time, math, threading, requests

CACHE = {"ts": 0.0, "data": None}
LOCK = threading.Lock()
TTL = 75

SYMBOLS = {
    "NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "INDIAVIX": "^INDIAVIX",
    "SP500": "^GSPC", "NASDAQ": "^IXIC", "DOW": "^DJI",
    "NIKKEI": "^N225", "HANGSENG": "^HSI",
    "OIL": "CL=F", "GOLD": "GC=F", "DXY": "DX-Y.NYB",
    "US10Y": "^TNX", "VIX": "^VIX", "USDINR": "INR=X"
}

def _quote(symbol):
    url = "https://query1.finance.yahoo.com/v8/finance/chart/" + requests.utils.quote(symbol, safe="")
    params = {"range": "2d", "interval": "1d", "includePrePost": "false"}
    r = requests.get(url, params=params, headers={"User-Agent":"Mozilla/5.0"}, timeout=5)
    r.raise_for_status()
    result = (r.json().get("chart") or {}).get("result") or []
    if not result:
        raise ValueError("empty quote")
    meta = result[0].get("meta") or {}
    price = meta.get("regularMarketPrice")
    prev = meta.get("previousClose") or meta.get("chartPreviousClose")
    if price is None:
        closes = ((result[0].get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
        vals = [float(x) for x in closes if x is not None]
        price = vals[-1] if vals else None
        prev = vals[-2] if len(vals) > 1 else prev
    change = ((float(price)-float(prev))/float(prev)*100) if price is not None and prev else 0.0
    return {"price": round(float(price),4) if price is not None else None, "change": round(change,3)}

def snapshot(force=False):
    now = time.time()
    with LOCK:
        if not force and CACHE["data"] is not None and now-CACHE["ts"] < TTL:
            return CACHE["data"]
    data = {}
    for name, ticker in SYMBOLS.items():
        try:
            data[name] = _quote(ticker)
        except Exception:
            data[name] = {"price": None, "change": None}
    # Scores are deliberately small: macro is a confirmation/risk filter,
    # not a replacement for the stock's technical model.
    def ch(k):
        v = data.get(k, {}).get("change")
        return float(v) if v is not None else 0.0
    risk = 0.0
    risk += max(-2.5, min(2.5, ch("SP500") * 0.9))
    risk += max(-2.5, min(2.5, ch("NASDAQ") * 0.9))
    risk += max(-2.0, min(2.0, ch("NIKKEI") * 0.6))
    risk += max(-2.0, min(2.0, ch("HANGSENG") * 0.6))
    risk -= max(-3.0, min(3.0, ch("VIX") * 0.8))
    risk -= max(-3.0, min(3.0, (ch("OIL")-0.2) * 0.7))
    risk -= max(-2.0, min(2.0, (ch("US10Y")) * 0.8))
    risk -= max(-2.0, min(2.0, (ch("DXY")) * 0.6))
    risk -= max(-2.0, min(2.0, (ch("USDINR")) * 0.8))
    india = 0.0
    india += max(-4.0, min(4.0, ch("NIFTY") * 1.4))
    india += max(-3.0, min(3.0, ch("BANKNIFTY") * 0.9))
    india -= max(-3.0, min(3.0, ch("INDIAVIX") * 0.9))
    data["_score"] = round(max(-20.0, min(20.0, risk + india)), 2)
    data["_global_score"] = round(max(-20.0, min(20.0, risk)), 2)
    data["_india_score"] = round(max(-12.0, min(12.0, india)), 2)
    data["_risk"] = "HIGH" if data["_score"] <= -7 else "LOW" if data["_score"] >= 7 else "NORMAL"
    data["_direction"] = "POSITIVE" if data["_score"] >= 2 else "NEGATIVE" if data["_score"] <= -2 else "MIXED"
    drivers = []
    if ch("OIL") > 1: drivers.append("Crude rising")
    if ch("OIL") < -1: drivers.append("Crude easing")
    if ch("US10Y") > 1: drivers.append("US yields rising")
    if ch("DXY") > 0.5: drivers.append("Dollar firm")
    if ch("VIX") > 3: drivers.append("VIX elevated")
    if ch("NASDAQ") > 0.5: drivers.append("Nasdaq positive")
    if ch("NASDAQ") < -0.5: drivers.append("Nasdaq weak")
    if ch("USDINR") > 0.3: drivers.append("Rupee pressure")
    data["_drivers"] = drivers[:5]
    data["_ts"] = now
    with LOCK:
        CACHE["data"], CACHE["ts"] = data, now
    return data

def enrich(row, base_enrich):
    ctx = snapshot()
    base_enrich(row, ctx.get("_base_market_ctx", {})) if False else None
    sym = str(row.get("symbol") or "").upper().replace("NSE:","").replace("-EQ","")
    sec = str(row.get("sector") or "Other")
    score = float(row.get("score") or 50)
    signal = str(row.get("signal") or "WAIT").upper()
    g = float(ctx.get("_score") or 0)
    # Sector-aware macro response.
    factor = 1.0
    if sec == "IT":
        factor += max(-0.6, min(0.6, (float(ctx.get("NASDAQ",{}).get("change") or 0))/3))
    elif sec in ("Auto","Consumer","Cement","Chemicals"):
        factor += max(-0.5, min(0.5, -(float(ctx.get("OIL",{}).get("change") or 0))/4))
    elif sec in ("Energy",):
        factor += max(-0.3, min(0.3, (float(ctx.get("OIL",{}).get("change") or 0))/5))
    elif sec in ("Pharma",):
        factor += max(-0.25, min(0.25, -(float(ctx.get("USDINR",{}).get("change") or 0))/4))
    impact = g * factor
    if signal == "BUY":
        score += impact * 0.65
    elif signal == "SELL":
        score -= impact * 0.65
    else:
        score += impact * 0.25
    score = max(0, min(100, score))
    macro_conf = (g >= 0 if signal == "BUY" else g <= 0 if signal == "SELL" else True)
    conf = row.get("confirmations") or {}
    conf["global"] = macro_conf
    row.update({
        "score": round(score,1),
        "global_impact": round(impact,2),
        "global_score": round(float(ctx.get("_global_score") or 0),2),
        "india_impact": round(float(ctx.get("_india_score") or 0),2),
        "macro_risk": ctx.get("_risk","NORMAL"),
        "macro_direction": ctx.get("_direction","MIXED"),
        "macro_drivers": ctx.get("_drivers",[]),
        "global_confirmed": macro_conf,
        "confirmations": conf
    })
    # Macro conflict can downgrade an otherwise borderline directional signal.
    if signal == "BUY" and impact < -3 and score < 72:
        row["signal"] = "WAIT"
    elif signal == "SELL" and impact > 3 and score > 28:
        row["signal"] = "WAIT"
    row["ai_reason"] = (row.get("ai_reason") or "") + " • Global + India macro impact filter"
    return row
