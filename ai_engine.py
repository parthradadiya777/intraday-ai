import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

MODEL_TTL = 6 * 60 * 60
_CACHE = {}
FEATURES = [
    'ret1', 'ret3', 'ret6', 'range_pct', 'body_pct',
    'ema_gap', 'rsi', 'vol_ratio', 'volatility', 'trend'
]


def _cols(df):
    return {str(c).lower().strip(): c for c in df.columns}


def _ohlcv(df):
    if df is None or len(df) < 30:
        return None
    c = _cols(df)
    close = c.get('close') or c.get('last')
    if not close:
        return None
    out = pd.DataFrame(index=df.index)
    out['close'] = pd.to_numeric(df[close], errors='coerce')
    for key, fallback in [('open', None), ('high', None), ('low', None), ('volume', None)]:
        col = c.get(key)
        if col:
            out[key] = pd.to_numeric(df[col], errors='coerce')
        elif fallback is None:
            out[key] = out['close'] if key != 'volume' else 1.0
    out = out.replace([np.inf, -np.inf], np.nan).dropna(subset=['close'])
    if len(out) < 30:
        return None
    return out


def _features(df):
    x = _ohlcv(df)
    if x is None:
        return None
    close = x['close']
    ret1 = close.pct_change()
    ret3 = close.pct_change(3)
    ret6 = close.pct_change(6)
    ema9 = close.ewm(span=9, adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rsi = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))
    rsi = rsi.fillna(50)
    candle_range = (x['high'] - x['low']).abs() / close.replace(0, np.nan)
    body = (x['close'] - x['open']).abs() / close.replace(0, np.nan)
    vol_mean = x['volume'].rolling(20).mean().replace(0, np.nan)
    vol_ratio = x['volume'] / vol_mean
    volatility = ret1.rolling(12).std()
    trend = (ema9 / ema21) - 1
    feat = pd.DataFrame({
        'ret1': ret1, 'ret3': ret3, 'ret6': ret6,
        'range_pct': candle_range, 'body_pct': body,
        'ema_gap': trend, 'rsi': rsi / 100.0,
        'vol_ratio': vol_ratio, 'volatility': volatility,
        'trend': (close / close.rolling(20).mean()) - 1,
    }, index=x.index)
    return x, feat.replace([np.inf, -np.inf], np.nan)


def _technical_probability(df):
    data = _features(df)
    if data is None:
        return 0.5, None
    x, f = data
    last = f.iloc[-1]
    p = 0.50
    p += 0.16 if last['ema_gap'] > 0 else -0.16
    p += 0.12 if last['ret3'] > 0 else -0.12
    p += 0.08 if 0.52 <= last['rsi'] <= 0.72 else (-0.08 if last['rsi'] < 0.42 else 0)
    p += 0.07 if last['vol_ratio'] > 1.15 else 0
    p += 0.07 if last['trend'] > 0 else -0.07
    return max(0.05, min(0.95, p)), last


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
        raw, feat = data
        future = raw['close'].shift(-1) / raw['close'] - 1
        # Predict the next 5-minute move; small moves are treated as noise.
        y = (future > 0.0015).astype(int)
        valid = feat[FEATURES].notna().all(axis=1) & future.notna()
        X = feat.loc[valid, FEATURES]
        Y = y.loc[valid]
        if len(X) < 160 or Y.nunique() < 2:
            return None
        split = int(len(X) * 0.80)
        X_train, X_test = X.iloc[:split], X.iloc[split:]
        y_train, y_test = Y.iloc[:split], Y.iloc[split:]
        from sklearn.ensemble import RandomForestClassifier
        model = RandomForestClassifier(
            n_estimators=120, max_depth=6, min_samples_leaf=5,
            class_weight='balanced_subsample', random_state=42, n_jobs=1
        )
        model.fit(X_train, y_train)
        accuracy = float(model.score(X_test, y_test)) if len(X_test) else 0.5
        item = {'model': model, 'accuracy': accuracy, 'ts': time.time()}
        _CACHE[symbol] = item
        return item
    except Exception:
        return None


def analyze(symbol, current_df):
    data = _features(current_df)
    if data is None:
        return None
    raw, feat = data
    p_tech, last = _technical_probability(current_df)
    trained = _train(symbol)
    model_used = False
    accuracy = None
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
    # Blend learned historical pattern with current-market structure.
    p = 0.75 * p_model + 0.25 * p_tech
    close = float(raw['close'].iloc[-1])
    ema9 = float(raw['close'].ewm(span=9, adjust=False).mean().iloc[-1])
    ema21 = float(raw['close'].ewm(span=21, adjust=False).mean().iloc[-1])
    atr_pct = float(((raw['high'] - raw['low']) / raw['close']).rolling(14).mean().iloc[-1])
    if not np.isfinite(atr_pct) or atr_pct <= 0:
        atr_pct = 0.008
    bullish_structure = close > ema9 > ema21
    bearish_structure = close < ema9 < ema21
    if p >= 0.68 and bullish_structure:
        signal = 'BUY'
    elif p <= 0.32 and bearish_structure:
        signal = 'SELL'
    else:
        signal = 'WAIT'
    score = round(p * 100, 1)
    confidence = round(max(p, 1 - p) * 100, 1)
    risk_pct = max(0.004, min(0.015, atr_pct * 1.15))
    reward_pct = max(0.008, min(0.025, atr_pct * 1.8))
    if signal == 'SELL':
        target = close * (1 - reward_pct)
        sl = close * (1 + risk_pct)
    else:
        target = close * (1 + reward_pct)
        sl = close * (1 - risk_pct)
    reasons = []
    reasons.append('ML historical pattern' if model_used else 'technical fallback')
    reasons.append('trend aligned' if bullish_structure or bearish_structure else 'trend mixed')
    reasons.append('volume active' if last is not None and last.get('vol_ratio', 0) > 1.15 else 'volume normal')
    return {
        'ai_probability': round(p, 4),
        'ai_confidence': confidence,
        'ai_direction': 'UP' if p >= 0.5 else 'DOWN',
        'ai_model': 'ML' if model_used else 'TECH-FALLBACK',
        'ai_validation': round(accuracy * 100, 1) if accuracy is not None else None,
        'score': score,
        'signal': signal,
        'target': round(target, 2),
        'sl': round(sl, 2),
        'ai_reason': ', '.join(reasons),
        'price': round(close, 2),
        'rsi': round(float(last['rsi'] * 100), 1) if last is not None else None,
        'ema9': round(ema9, 2),
        'ema21': round(ema21, 2),
        'momentum': round(float(last['ret3'] * 100), 3) if last is not None else None,
    }
