import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

MODEL_TTL = 6 * 60 * 60
_CACHE = {}
FEATURES = [
    'ret1', 'ret3', 'ret6', 'range_pct', 'body_pct', 'ema_gap', 'rsi',
    'vol_ratio', 'volatility', 'trend', 'vwap_gap', 'macd_gap', 'adx',
    'atr_pct', 'breakout_gap'
]


def _cols(df):
    return {str(c).lower().strip(): c for c in df.columns}


def _ohlcv(df):
    if df is None or len(df) < 40:
        return None
    c = _cols(df)
    close_col = c.get('close') or c.get('last')
    if close_col is None:
        return None
    out = pd.DataFrame(index=df.index)
    out['close'] = pd.to_numeric(df[close_col], errors='coerce')
    for key in ('open', 'high', 'low', 'volume'):
        col = c.get(key)
        if col is not None:
            out[key] = pd.to_numeric(df[col], errors='coerce')
        elif key == 'volume':
            out[key] = 1.0
        else:
            out[key] = out['close']
    out = out.replace([np.inf, -np.inf], np.nan).dropna(subset=['close'])
    if len(out) < 40:
        return None
    return out


def _rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def _adx(high, low, close, period=14):
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean().replace(0, np.nan)
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False).mean().fillna(0), plus_di.fillna(0), minus_di.fillna(0), tr


def _vwap(x):
    typical = (x['high'] + x['low'] + x['close']) / 3.0
    pv = typical * x['volume'].clip(lower=0)
    # Reset VWAP at each trading day when a datetime index is available.
    try:
        dates = pd.to_datetime(x.index).date
        return pv.groupby(dates).cumsum() / x['volume'].clip(lower=0).groupby(dates).cumsum().replace(0, np.nan)
    except Exception:
        return pv.cumsum() / x['volume'].clip(lower=0).cumsum().replace(0, np.nan)


def _features(df):
    x = _ohlcv(df)
    if x is None:
        return None
    close, high, low = x['close'], x['high'], x['low']
    ret1, ret3, ret6 = close.pct_change(), close.pct_change(3), close.pct_change(6)
    ema9 = close.ewm(span=9, adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    rsi = _rsi(close)
    candle_range = (high - low).abs() / close.replace(0, np.nan)
    body = (close - x['open']).abs() / close.replace(0, np.nan)
    vol_mean = x['volume'].rolling(20, min_periods=5).mean().replace(0, np.nan)
    vol_ratio = x['volume'] / vol_mean
    volatility = ret1.rolling(12, min_periods=5).std()
    trend = close / close.rolling(20, min_periods=5).mean() - 1
    vwap = _vwap(x)
    vwap_gap = close / vwap.replace(0, np.nan) - 1
    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    macd_gap = (macd - macd_signal) / close.replace(0, np.nan)
    adx, plus_di, minus_di, tr = _adx(high, low, close)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    atr_pct = atr / close.replace(0, np.nan)
    prior_high = high.shift(1).rolling(20, min_periods=10).max()
    prior_low = low.shift(1).rolling(20, min_periods=10).min()
    # Positive = breakout above recent range; negative = breakdown below it.
    breakout_gap = np.where(close > prior_high, close / prior_high - 1,
                            np.where(close < prior_low, close / prior_low - 1, 0.0))
    feat = pd.DataFrame({
        'ret1': ret1, 'ret3': ret3, 'ret6': ret6,
        'range_pct': candle_range, 'body_pct': body,
        'ema_gap': (ema9 / ema21) - 1, 'rsi': rsi / 100.0,
        'vol_ratio': vol_ratio, 'volatility': volatility, 'trend': trend,
        'vwap_gap': vwap_gap, 'macd_gap': macd_gap, 'adx': adx / 100.0,
        'atr_pct': atr_pct, 'breakout_gap': breakout_gap,
    }, index=x.index)
    indicators = {
        'vwap': vwap, 'ema9': ema9, 'ema21': ema21, 'ema50': ema50,
        'rsi': rsi, 'macd': macd, 'macd_signal': macd_signal,
        'adx': adx, 'plus_di': plus_di, 'minus_di': minus_di,
        'atr': atr, 'atr_pct': atr_pct, 'vol_ratio': vol_ratio,
        'breakout_gap': pd.Series(breakout_gap, index=x.index),
        'prior_high': prior_high, 'prior_low': prior_low,
    }
    return x, feat.replace([np.inf, -np.inf], np.nan), indicators


def _technical_probability(df):
    data = _features(df)
    if data is None:
        return 0.5, None, None
    x, f, ind = data
    last = f.iloc[-1]
    p = 0.50
    # Independent confirmations: trend, VWAP, momentum, volume, MACD and DI.
    p += 0.10 if last['ema_gap'] > 0 else -0.10
    p += 0.08 if last['vwap_gap'] > 0 else -0.08
    p += 0.08 if last['ret3'] > 0 else -0.08
    p += 0.07 if 0.52 <= last['rsi'] <= 0.72 else (-0.07 if last['rsi'] < 0.42 else 0)
    p += 0.07 if last['macd_gap'] > 0 else -0.07
    p += 0.06 if last['vol_ratio'] > 1.15 else 0
    p += 0.05 if last['trend'] > 0 else -0.05
    p += 0.05 if last['plus_di'] > last['minus_di'] and last['adx'] >= 0.20 else (-0.05 if last['minus_di'] > last['plus_di'] and last['adx'] >= 0.20 else 0)
    p += 0.05 if last['breakout_gap'] > 0 else (-0.05 if last['breakout_gap'] < 0 else 0)
    return max(0.05, min(0.95, p)), last, ind


def _train(symbol):
    cached = _CACHE.get(symbol)
    if cached and time.time() - cached['ts'] < MODEL_TTL:
        return cached
    try:
        from nsemine import historical
        end = datetime.now()
        start = end - timedelta(days=25)
        df = historical.get_stock_historical_data(symbol, start, end, interval=5)
        data = _features(df)
        if data is None:
            return None
        raw, feat, _ = data
        future = raw['close'].shift(-1) / raw['close'] - 1
        y = (future > 0.0015).astype(int)
        valid = feat[FEATURES].notna().all(axis=1) & future.notna()
        X, Y = feat.loc[valid, FEATURES], y.loc[valid]
        if len(X) < 160 or Y.nunique() < 2:
            return None
        split = int(len(X) * 0.80)
        from sklearn.ensemble import RandomForestClassifier
        model = RandomForestClassifier(
            n_estimators=160, max_depth=7, min_samples_leaf=5,
            class_weight='balanced_subsample', random_state=42, n_jobs=1
        )
        model.fit(X.iloc[:split], Y.iloc[:split])
        accuracy = float(model.score(X.iloc[split:], Y.iloc[split:])) if len(X) > split else 0.5
        item = {'model': model, 'accuracy': accuracy, 'ts': time.time()}
        _CACHE[symbol] = item
        return item
    except Exception:
        return None


def analyze(symbol, current_df):
    data = _features(current_df)
    if data is None:
        return None
    raw, feat, ind = data
    p_tech, last, indicators = _technical_probability(current_df)
    if last is None:
        return None
    trained = _train(symbol)
    model_used, accuracy = False, None
    p_model = p_tech
    if trained is not None:
        row = feat.iloc[[-1]][FEATURES]
        if row.notna().all(axis=None):
            try:
                p_model = float(trained['model'].predict_proba(row)[0][1])
                accuracy = trained['accuracy']
                model_used = True
            except Exception:
                pass
    p = 0.70 * p_model + 0.30 * p_tech
    close = float(raw['close'].iloc[-1])
    ema9 = float(indicators['ema9'].iloc[-1])
    ema21 = float(indicators['ema21'].iloc[-1])
    ema50 = float(indicators['ema50'].iloc[-1])
    vwap = float(indicators['vwap'].iloc[-1]) if np.isfinite(indicators['vwap'].iloc[-1]) else close
    rsi = float(indicators['rsi'].iloc[-1])
    macd = float(indicators['macd'].iloc[-1])
    macd_signal = float(indicators['macd_signal'].iloc[-1])
    adx = float(indicators['adx'].iloc[-1])
    plus_di = float(indicators['plus_di'].iloc[-1])
    minus_di = float(indicators['minus_di'].iloc[-1])
    atr = float(indicators['atr'].iloc[-1])
    atr_pct = float(indicators['atr_pct'].iloc[-1])
    vol_ratio = float(indicators['vol_ratio'].iloc[-1])
    breakout_gap = float(indicators['breakout_gap'].iloc[-1])

    bullish = close > vwap and ema9 > ema21 and ema21 >= ema50 and plus_di >= minus_di
    bearish = close < vwap and ema9 < ema21 and ema21 <= ema50 and minus_di >= plus_di
    momentum_bull = rsi >= 52 and macd >= macd_signal
    momentum_bear = rsi <= 48 and macd <= macd_signal
    volume_ok = vol_ratio >= 1.05
    trend_ok = adx >= 20
    breakout_ok = breakout_gap > 0
    breakdown_ok = breakout_gap < 0

    # Require multiple confirmations rather than creating a signal just to fill the UI.
    buy_checks = sum([bullish, momentum_bull, volume_ok, trend_ok, breakout_ok, p >= 0.64])
    sell_checks = sum([bearish, momentum_bear, volume_ok, trend_ok, breakdown_ok, p <= 0.36])
    if buy_checks >= 4 and bullish and p >= 0.64:
        signal = 'BUY'
    elif sell_checks >= 4 and bearish and p <= 0.36:
        signal = 'SELL'
    else:
        signal = 'WAIT'

    # ATR-based risk instead of fixed percentages.
    risk_pct = max(0.004, min(0.018, atr_pct * 1.15 if np.isfinite(atr_pct) else 0.008))
    reward_pct = max(0.008, min(0.035, risk_pct * 2.0))
    if signal == 'SELL':
        target, sl = close * (1 - reward_pct), close * (1 + risk_pct)
    else:
        target, sl = close * (1 + reward_pct), close * (1 - risk_pct)

    confirmations = {
        'vwap': close > vwap if signal == 'BUY' else close < vwap if signal == 'SELL' else close >= vwap,
        'ema': ema9 > ema21 if signal == 'BUY' else ema9 < ema21 if signal == 'SELL' else False,
        'rsi': rsi >= 52 if signal == 'BUY' else rsi <= 48 if signal == 'SELL' else False,
        'macd': macd >= macd_signal if signal == 'BUY' else macd <= macd_signal if signal == 'SELL' else False,
        'adx': adx >= 20,
        'relative_volume': vol_ratio >= 1.05,
        'breakout': breakout_ok if signal == 'BUY' else breakdown_ok if signal == 'SELL' else False,
    }
    reason_parts = [k.upper() for k, ok in confirmations.items() if ok]
    reason = 'confirmed: ' + ', '.join(reason_parts) if reason_parts else 'mixed conditions'
    return {
        'ai_probability': round(p, 4), 'ai_confidence': round(max(p, 1 - p) * 100, 1),
        'ai_direction': 'UP' if p >= 0.5 else 'DOWN',
        'ai_model': 'ML+MULTI-FACTOR' if model_used else 'MULTI-FACTOR',
        'ai_validation': round(accuracy * 100, 1) if accuracy is not None else None,
        'score': round(p * 100, 1), 'signal': signal,
        'target': round(target, 2), 'sl': round(sl, 2),
        'ai_reason': reason,
        'price': round(close, 2), 'rsi': round(rsi, 1),
        'ema9': round(ema9, 2), 'ema21': round(ema21, 2),
        'vwap': round(vwap, 2), 'macd': round(macd, 4),
        'macd_signal': round(macd_signal, 4), 'adx': round(adx, 1),
        'relative_volume': round(vol_ratio, 2), 'atr': round(atr, 2),
        'atr_pct': round(atr_pct * 100, 3),
        'breakout_gap': round(breakout_gap * 100, 3),
        'momentum': round(float(last['ret3'] * 100), 3),
    }
