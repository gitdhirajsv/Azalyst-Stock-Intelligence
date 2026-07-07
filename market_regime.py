import numpy as np
import pandas as pd

def detect_bull_regime(benchmark_df):
    """
    Check J Law's 8 bottom signals (simplified).
    Returns dict of signals and overall bull boolean.
    """
    df = benchmark_df.copy()
    signals = {}
    last = df.iloc[-1]

    # 1. Extreme oversold: 20-day drop > 20% in last year
    roc20 = df['Close'].pct_change(20)
    signals['extreme_oversold'] = roc20.min() < -0.20

    # 2. Powerful rally: price up 10% from 20-day low
    low20 = df['Close'].rolling(20).min().iloc[-1]
    signals['powerful_rally'] = last['Close'] > low20 * 1.10

    # 3. Golden cross: 50MA > 200MA
    ma50 = last.get('MA_50')
    ma200 = last.get('MA_200')
    if pd.notna(ma50) and pd.notna(ma200):
        signals['golden_cross'] = ma50 > ma200
    else:
        signals['golden_cross'] = False

    # 4. 200MA slope up
    if pd.notna(ma200):
        if len(df) > 20:
            slope200 = (ma200 - df['MA_200'].iloc[-21]) / 20
            signals['ma200_up'] = slope200 > 0
        else:
            signals['ma200_up'] = False
    else:
        signals['ma200_up'] = False

    # 5. Broad participation proxy: we'll skip (requires internals data)
    signals['broad_participation'] = None

    # Confirmed uptrend (standard "risk-on" for a trend-following stock system):
    # benchmark above its 200MA, golden cross in effect, and the 200MA rising.
    price = last.get('Close')
    if pd.notna(ma50) and pd.notna(ma200) and pd.notna(price):
        signals['uptrend'] = bool(
            (price > ma200) and (ma50 > ma200) and bool(signals['ma200_up'])
        )
    else:
        signals['uptrend'] = False

    # Normalize to native Python bools. pandas/numpy comparisons return numpy.bool_,
    # and `np.True_ is True` is False, so an identity-based count silently reads 0
    # and the regime is stuck BEAR forever. bool() also keeps the dict JSON-safe.
    signals = {k: (None if v is None else bool(v)) for k, v in signals.items()}

    # Count known signals (ignore None / unknown internals).
    true_count = sum(1 for v in signals.values() if v is True)
    total_known = sum(1 for v in signals.values() if v is not None)
    score = true_count / total_known if total_known > 0 else 0.5

    # Bull if the benchmark is in a confirmed uptrend, OR the broader J Law
    # bottom-signal cluster fires (early turn off a washout low).
    is_bull = bool(signals['uptrend']) or ((true_count >= 3) and (score >= 0.6))
    return {"signals": signals, "is_bull": is_bull}
