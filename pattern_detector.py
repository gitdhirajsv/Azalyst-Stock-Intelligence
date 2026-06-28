import numpy as np
from utils import find_swing_highs_lows, draw_trendline
from scipy.signal import argrelextrema

def detect_vcp(df, lookback=180):
    """
    Detect VCP pattern: contracting amplitudes, volume dry-up, tight area.
    Returns dict with pivot and flags, or None.
    """
    if len(df) < lookback:
        return None
    seg = df.iloc[-lookback:].copy()
    highs = seg['High'].values
    lows = seg['Low'].values
    closes = seg['Close'].values
    volumes = seg['Volume'].values

    # Find swings
    high_idx, low_idx = find_swing_highs_lows(seg['High'], order=5)
    if len(high_idx) < 2 or len(low_idx) < 2:
        return None

    # Get last few swings (within last 150 days of segment)
    recent_highs_idx = [i for i in high_idx if i > len(seg) - 150]
    recent_lows_idx = [i for i in low_idx if i > len(seg) - 150]
    if len(recent_highs_idx) < 3 or len(recent_lows_idx) < 2:
        return None

    recent_highs = highs[recent_highs_idx]
    recent_lows = lows[recent_lows_idx]

    # Contractions: last amplitude should be <10%
    last_amp = (recent_highs[-1] - recent_lows[-1]) / recent_highs[-1]
    if last_amp > 0.10:
        return None

    # Volume: second half < first half
    half = len(volumes)//2
    if half < 10:
        return None
    vol1 = np.mean(volumes[:half])
    vol2 = np.mean(volumes[half:])
    if vol2 > vol1 * 0.8:
        return None

    pivot = recent_highs[-1]
    # Breakout condition: close > pivot and volume > 50MA volume?
    # We'll let signal generator decide, but here we just return the pivot info.
    return {
        'pivot': pivot,
        'contraction_pct': last_amp * 100,
        'vol_contracting': vol2 < vol1 * 0.8,
        'breakout_now': closes[-1] > pivot and volumes[-1] > np.mean(volumes[-20:]) * 1.5,
        'close': closes[-1],
        'volume': volumes[-1]
    }

def detect_meta_pullback(df, pivot, lookback=30):
    """
    Check if current price is in a M.E.T.A. pullback zone.
    """
    last = df.iloc[-1]
    close = last['Close']

    edges = 0
    # Breakout Level (BOL) zone: within 2% of pivot
    if abs(close - pivot) / pivot <= 0.02:
        edges += 1

    # MA support: near 10/20/50MA
    for ma in ['MA_10','MA_20','MA_50']:
        if ma in df.columns:
            ma_val = last[ma]
            if pd.notna(ma_val) and abs(close - ma_val) / close <= 0.02:
                edges += 1
                break

    # Volume contraction: lower than 50% of 50-day avg
    vol_ma50 = last.get('Volume_MA_50', np.inf)
    if last['Volume'] < vol_ma50 * 0.5:
        edges += 1

    # Trendline support (simple)
    seg = df.iloc[-lookback:]
    lows_idx = argrelextrema(seg['Low'].values, np.less, order=3)[0]
    if len(lows_idx) >= 2:
        x = lows_idx[-2:]
        y = seg['Low'].iloc[lows_idx[-2:]].values
        slope, intercept = draw_trendline(x, y)
        pred = slope * (len(seg)-1) + intercept
        if abs(close - pred) / close <= 0.03:
            edges += 1

    return {
        'meta_score': edges,
        'is_meta': edges >= 2
    }
