import numpy as np

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

    # Count true signals (ignore None)
    true_count = sum(v for v in signals.values() if v is True)
    total_known = sum(v is not None for v in signals.values())
    score = true_count / total_known if total_known > 0 else 0.5

    is_bull = (true_count >= 3) and (score >= 0.6)
    return signals, is_bull
