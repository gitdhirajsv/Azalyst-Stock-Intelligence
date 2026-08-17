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
from config import RS_RATING_MIN, MIN_PRICE

# ---- Trend Template thresholds (Minervini, "Trade Like a Stock Market Wizard") ----
PCT_ABOVE_52W_LOW = 0.30       # price must be >= 30% above its 52-week low (book value)
WITHIN_52W_HIGH = 0.15         # price within 15% of 52w high (personalized standard; was 25%)
# RS_RATING_MIN now comes from config (89 == Minervini's watershed line).
MA200_UPTREND_LOOKBACK = 21    # 200-day MA must be rising over ~1 month (21 trading days)
MIN_BARS = 252                 # need a full year of data for 52w range + RS

# ---- Buy-point / risk parameters (VCP pivot) ----
BUY_ZONE_PCT = 0.05            # valid buy only within 5% above the pivot (else extended)
BREAKOUT_VOL_MULT = 1.4        # breakout-day volume >= 1.4x the 50-day average volume
FINAL_CONTRACTION_MAX_PCT = 10.0   # final contraction must be tight (<= 10%)
STOP_MAX_PCT = 0.08            # a valid pivot buy risks <= 8%; looser base -> reject, don't chase
TARGET_R = 3.0                 # reference profit target = 3R (Minervini sells into strength)

# ---- Timing vocabulary (S3) ----
# Every call carries an explicit NUMERIC trigger_price: the price at which the
# name becomes (or becomes again) a valid low-risk entry. BUY NOW is emitted by
# minervini_signal() itself and means the hard gates pass right now. The other
# three are WATCH-tier states produced by minervini_watch() for names that pass
# the structural gates but are not buyable at today's price -- they are NOT
# entry signals and never reach the risk manager or the paper trader.
TIMING_BUY_NOW = "BUY NOW"                       # gates pass now -> executable
TIMING_BUY_ON_BREAKOUT = "BUY ON BREAKOUT"       # at the pivot, breakout unconfirmed
TIMING_WAIT_FOR_BREAKOUT = "WAIT FOR BREAKOUT"   # base formed, price still under the pivot
TIMING_WAIT_FOR_PULLBACK = "WAIT FOR PULLBACK"   # extended / stop too far -> not buyable here

AT_PIVOT_TOL = 0.02            # within 2% under the pivot counts as "coiled at" it


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


def max_entry_price(pivot, base_low=None):
    """Highest price at which a pivot buy still passes BOTH hard entry gates.

    Gate 1 (buy zone):  close <= pivot * (1 + BUY_ZONE_PCT)
    Gate 2 (max risk):  (close - base_low) / close <= STOP_MAX_PCT
                        <=>  close <= base_low / (1 - STOP_MAX_PCT)
                        (only when a real base_low is available; detect_vcp
                        always supplies one, so the buy-zone-only fallback
                        below is a defensive default, not a modeled gate)

    Whichever binds first is the number a WAIT FOR PULLBACK alert should fire
    at: the price an extended name has to come back to before minervini_signal()
    could fire on it. This is derived from the existing gates, it does not
    relax them.
    """
    ceiling = float(pivot) * (1 + BUY_ZONE_PCT)
    if base_low is not None and pd.notna(base_low) and base_low > 0:
        ceiling = min(ceiling, float(base_low) / (1 - STOP_MAX_PCT))
    return float(ceiling)


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

    # Gate: minimum price (Minervini avoids low-priced stocks). J Law path enforces
    # this via the Stage-2 screen; the Minervini path applies it here directly.
    if close < MIN_PRICE:
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
        'timing': TIMING_BUY_NOW,
        'trigger_price': round(entry, 4),   # buyable at this price, right now
        'actionable_now': True,
        'trend_template': checks,
        'reason': (
            f"Minervini Trend Template PASS + VCP pivot breakout @ {pivot:.2f} "
            f"(RS {rs_rating if rs_rating is not None else 'n/a'}); "
            f"buy {entry:.2f}, stop {stop:.2f} ({risk / entry * 100:.1f}% risk), target {target:.2f}"
        ),
    }


def minervini_watch(ticker, df, rs_rating=None):
    """WATCH-tier record for a name that passes the Minervini STRUCTURAL gates
    (Trend Template + a tight VCP base) but is not buyable at today's price.

    S3: minervini_signal() rejects and then DISCARDS these names -- a breakout
    extended beyond the buy zone (close > pivot*1.05), a base whose structural
    stop implies more than STOP_MAX_PCT risk, and a base still coiling under its
    pivot. All three are TIMING rejections, not quality rejections, and the
    buy-zone window they describe is exactly the window this strategy trades. A
    dropped name left no record at all, so there was no alert level to act on
    and nothing to measure screened-but-not-traded hit rates against.

    A watch entry is deliberately NOT executable: it carries no 'price' and no
    'stop_loss' key, `actionable_now` is False, and it is returned on a separate
    list that never reaches the risk manager or paper trader. The only way it
    becomes a position is by later passing minervini_signal()'s unchanged hard
    gates on its own.

    Returns a watch dict, or None when the name is not a structural candidate --
    or when it IS a valid entry right now, which is minervini_signal()'s job.
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

    # Same non-timing gates the entry path applies: a low-priced stock or a
    # loose, sloppy base is not a candidate at ANY price, so it is not a watch.
    if close < MIN_PRICE:
        return None
    if vcp.get('contraction_pct', 100.0) > FINAL_CONTRACTION_MAX_PCT:
        return None

    base_low = vcp.get('base_low')
    buy_zone_high = pivot * (1 + BUY_ZONE_PCT)
    max_entry = max_entry_price(pivot, base_low)

    # A confirmed in-zone breakout is an ENTRY, not a watch.
    #
    # KNOWN GAP: detect_vcp's `breakout_now` uses a looser volume reference
    # (mean of the prior 50 bars, EXCLUDING today) than minervini_signal's own
    # gate (Volume_MA_50, a rolling mean that INCLUDES today), and
    # minervini_signal separately rejects an inverted/too-wide stop. A name can
    # therefore satisfy `breakout_now and close <= max_entry` here yet still be
    # rejected by minervini_signal's stricter checks -- that name is currently
    # neither an entry nor a watch. Deliberately not closed by delegating to
    # minervini_signal() here: that call needs df['Volume']/df['Volume_MA_50'],
    # which would make minervini_watch's cost and failure modes depend on
    # columns it otherwise never touches (this module's own tests build a
    # Close-only frame for exactly that reason). Left as a documented gap
    # rather than a fix that trades a silent drop for a new crash surface.
    if vcp.get('breakout_now') and close <= max_entry:
        return None

    if close > max_entry:
        timing = TIMING_WAIT_FOR_PULLBACK
        trigger = max_entry
        detail = (
            f"Price {close:.2f} is {((close / max_entry) - 1) * 100:.1f}% above the highest "
            f"price this pivot is still buyable at ({max_entry:.2f}) -- chasing here breaks "
            f"the {BUY_ZONE_PCT:.0%} buy zone (tops at {buy_zone_high:.2f}) and/or the "
            f"{STOP_MAX_PCT:.0%} max-risk stop. Alert on a pullback to {max_entry:.2f}."
        )
    elif close >= pivot:
        timing = TIMING_BUY_ON_BREAKOUT
        trigger = float(pivot)
        detail = (
            f"Price {close:.2f} is through the pivot {pivot:.2f} and still inside the buy "
            f"zone, but the breakout is unconfirmed -- it needs >= {BREAKOUT_VOL_MULT}x the "
            f"50-day average volume. Buy only on a volume-confirmed close."
        )
    elif close >= pivot * (1 - AT_PIVOT_TOL):
        timing = TIMING_BUY_ON_BREAKOUT
        trigger = float(pivot)
        detail = (
            f"Coiling {((pivot / close) - 1) * 100:.1f}% under the VCP pivot {pivot:.2f}. Buy "
            f"the move through {pivot:.2f} only if volume expands to "
            f">= {BREAKOUT_VOL_MULT}x the 50-day average."
        )
    else:
        timing = TIMING_WAIT_FOR_BREAKOUT
        trigger = float(pivot)
        detail = (
            f"A VCP base is formed but price {close:.2f} is "
            f"{((pivot / close) - 1) * 100:.1f}% below the pivot {pivot:.2f}. No entry until "
            f"it clears the pivot."
        )

    # Reference only -- named stop_reference, NOT stop_loss, so nothing downstream
    # can mistake a watch entry for a sized, stop-protected trade.
    stop_reference = None
    if base_low is not None and pd.notna(base_low) and 0 < base_low < trigger:
        stop_reference = float(base_low)
    projected_risk_pct = (
        round((trigger - stop_reference) / trigger * 100, 2) if stop_reference else None
    )

    return {
        'ticker': ticker,
        'kind': 'WATCH',
        'type': 'WATCH',
        'source': 'MINERVINI',
        'strategy': 'Minervini SEPA / VCP',
        'timing': timing,
        'timing_detail': detail,
        'trigger_price': round(float(trigger), 4),
        'pivot': round(float(pivot), 4),
        'buy_zone_high': round(float(buy_zone_high), 4),
        'last_close': round(float(close), 4),
        'stop_reference': round(stop_reference, 4) if stop_reference else None,
        'projected_risk_pct': projected_risk_pct,
        'rs_rating': rs_rating,
        'actionable_now': False,
        'trend_template': checks,
        'reason': f"WATCH ({timing}) trigger {trigger:.2f} -- {detail}",
    }
