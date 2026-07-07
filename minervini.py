"""
Mark Minervini SEPA / Trend Template stock selection + VCP pivot buy point.

This is an INDEPENDENT signal path that runs alongside the existing J Law path.
A Minervini BUY only fires when a stock:
  1) passes the 8-point Trend Template (Stage-2 leadership), AND
  2) is a FRESH VCP pivot breakout sitting inside a valid low-risk buy zone
     (i.e. the signal is AT A BUYING PRICE, not extended/chasing), on expanding
     volume — with a protective stop and a measured target.

References: Minervini, "Trade Like a Stock Market Wizard" (Trend Template, VCP);
IBD-style Relative Strength (RS) Rating.
"""
import numpy as np
import pandas as pd

from pattern_detector import detect_vcp

# ---- Trend Template thresholds (Minervini, "Trade Like a Stock Market Wizard") ----
PCT_ABOVE_52W_LOW = 0.30       # price must be >= 30% above its 52-week low (book value)
WITHIN_52W_HIGH = 0.25         # price must be within 25% of its 52-week high
RS_RATING_MIN = 70             # RS Rating >= 70 (Minervini prefers 80-90+)
MA200_UPTREND_LOOKBACK = 21    # 200-day MA must be rising over ~1 month (21 trading days)
MIN_BARS = 252                 # need a full year of data for 52w range + RS

# ---- Buy-point / risk parameters (VCP pivot) ----
BUY_ZONE_PCT = 0.05            # valid buy only within 5% above the pivot (else extended)
BREAKOUT_VOL_MULT = 1.4        # breakout-day volume >= 1.4x the 50-day average volume
FINAL_CONTRACTION_MAX_PCT = 10.0   # final contraction must be tight (<= 10%)
STOP_MAX_PCT = 0.08            # a valid pivot buy risks <= 8%; looser base -> reject, don't chase
TARGET_R = 3.0                 # reference profit target = 3R (Minervini sells into strength)


def compute_rs_ratings(stock_data):
    """
    IBD-style Relative Strength RATING (1-99 percentile) across the whole universe.

    Uses a weighted trailing return with the most recent quarter double-weighted:
        rs_raw = 2*(C/C_63) + (C/C_126) + (C/C_189) + (C/C_252)
    (63/126/189/252 trading days ~= 1/2/3/4 quarters), then percentile-ranks the
    raw score across all tickers into 1..99. This is the cross-sectional RS RATING,
    which is distinct from the RS LINE (price/benchmark ratio) used elsewhere.

    Returns: dict {ticker: int rating in 1..99}
    """
    perf = {}
    for ticker, df in stock_data.items():
        if df is None or len(df) < MIN_BARS:
            continue
        c = df['Close'].dropna()
        if len(c) < MIN_BARS:
            continue
        c0 = c.iloc[-1]
        c63, c126, c189, c252 = c.iloc[-63], c.iloc[-126], c.iloc[-189], c.iloc[-252]
        if min(c63, c126, c189, c252) <= 0 or c0 <= 0:
            continue
        rs_raw = 2.0 * (c0 / c63) + (c0 / c126) + (c0 / c189) + (c0 / c252)
        if np.isfinite(rs_raw):
            perf[ticker] = rs_raw

    if not perf:
        return {}

    s = pd.Series(perf)
    # Percentile rank -> 1..99 (best performer ~99, worst ~1). Coerce to native int so
    # rs_rating serializes as a JSON number, not a stringified numpy scalar.
    ratings = (s.rank(pct=True) * 98 + 1).round().clip(1, 99).astype(int)
    return {t: int(v) for t, v in ratings.items()}


def passes_trend_template(df, rs_rating=None):
    """
    Minervini 8-point Trend Template. Returns (passed: bool, checks: dict).

    If rs_rating is None (not enough universe data) the RS check is treated as
    passing so the structural criteria still gate the stock.
    """
    if df is None or len(df) < MIN_BARS:
        return False, {}
    last = df.iloc[-1]
    close = last.get('Close')
    ma50, ma150, ma200 = last.get('MA_50'), last.get('MA_150'), last.get('MA_200')
    if any(pd.isna(x) for x in [close, ma50, ma150, ma200]):
        return False, {}

    win = df.iloc[-MIN_BARS:]                 # trailing 52-week window
    high52 = win['High'].max()
    low52 = win['Low'].min()
    if pd.isna(high52) or pd.isna(low52) or low52 <= 0:
        return False, {}

    # 200-day MA trending up over the lookback window (~1 month).
    ma200_series = df['MA_200'] if 'MA_200' in df.columns else None
    ma200_ago = np.nan
    if ma200_series is not None and len(ma200_series) > MA200_UPTREND_LOOKBACK:
        ma200_ago = ma200_series.iloc[-1 - MA200_UPTREND_LOOKBACK]
    ma200_up = bool(pd.notna(ma200_ago) and ma200 > ma200_ago)

    checks = {
        'c1_price_above_150_200': bool(close > ma150 and close > ma200),
        'c2_150_above_200':       bool(ma150 > ma200),
        'c3_200_uptrending':      ma200_up,
        'c4_50_above_150_200':    bool(ma50 > ma150 and ma50 > ma200),
        'c5_price_above_50':      bool(close > ma50),
        'c6_30pct_above_low':     bool(close >= low52 * (1 + PCT_ABOVE_52W_LOW)),
        'c7_within_25pct_high':   bool(close >= high52 * (1 - WITHIN_52W_HIGH)),
        'c8_rs_rating_ok':        bool(rs_rating is None or rs_rating >= RS_RATING_MIN),
    }
    passed = all(checks.values())
    checks['rs_rating'] = rs_rating
    return passed, checks


def minervini_signal(ticker, df, rs_rating=None):
    """
    Return a Minervini BUY signal dict if (and only if) the trend template passes
    AND price is a fresh VCP pivot breakout inside the valid buy zone on expanding
    volume. Otherwise return None.

    The returned 'price' is the executable buy price (current close, just above the
    pivot and still inside the buy zone) so the signal is AT A BUYING PRICE.
    """
    passed, checks = passes_trend_template(df, rs_rating)
    if not passed:
        return None

    vcp = detect_vcp(df)
    if vcp is None:
        return None

    pivot = vcp.get('pivot', 0.0)
    close = vcp.get('close', 0.0)
    if pivot <= 0 or close <= 0:
        return None

    # Gate: fresh pivot breakout THIS bar (close above pivot on expanding volume).
    if not vcp.get('breakout_now'):
        return None

    # Gate: the final contraction must be tight (not a loose, sloppy base).
    if vcp.get('contraction_pct', 100.0) > FINAL_CONTRACTION_MAX_PCT:
        return None

    # Gate: price must sit INSIDE the buy zone (pivot .. pivot*1.05). Above that the
    # move is extended/chasing and the stop would be too far -> not a low-risk buy.
    buy_zone_high = pivot * (1 + BUY_ZONE_PCT)
    if close > buy_zone_high:
        return None

    # Gate: breakout-day volume confirmation vs the 50-day average.
    vol = df['Volume'].iloc[-1]
    vol_ma50 = df['Volume_MA_50'].iloc[-1] if 'Volume_MA_50' in df.columns else np.nan
    if pd.isna(vol) or pd.isna(vol_ma50) or vol_ma50 <= 0 or vol < vol_ma50 * BREAKOUT_VOL_MULT:
        return None

    entry = float(close)  # executable now, inside the buy zone (at buying price)
    # Stop just below the final-contraction low. If that structural stop is looser
    # than STOP_MAX_PCT, this is not a tight low-risk pivot buy -> reject (don't chase).
    base_low = vcp.get('base_low')
    if base_low is not None and pd.notna(base_low) and 0 < base_low < entry:
        stop = float(base_low)
    else:
        stop = pivot * (1 - STOP_MAX_PCT)   # fallback when base low unavailable
    if stop >= entry:
        return None                          # inverted/bad pivot data -> no non-positive risk
    risk = entry - stop
    if risk / entry > STOP_MAX_PCT:
        return None                          # stop too loose -> not a low-risk pivot buy

    target = entry + TARGET_R * risk

    return {
        'ticker': ticker,
        'type': 'BUY_BREAKOUT',
        'source': 'MINERVINI',
        'strategy': 'Minervini SEPA / VCP',
        'price': round(entry, 4),          # buy price (paper trader fills here)
        'pivot': round(float(pivot), 4),
        'buy_zone_high': round(float(buy_zone_high), 4),
        'stop_loss': round(float(stop), 4),
        'target': round(float(target), 4),
        'risk_pct': round(risk / entry * 100, 2),
        'rs_rating': rs_rating,
        'actionable_now': True,
        'trend_template': checks,
        'reason': (
            f"Minervini Trend Template PASS + VCP pivot breakout @ {pivot:.2f} "
            f"(RS {rs_rating if rs_rating is not None else 'n/a'}); "
            f"buy {entry:.2f}, stop {stop:.2f} ({risk / entry * 100:.1f}% risk), target {target:.2f}"
        ),
    }
