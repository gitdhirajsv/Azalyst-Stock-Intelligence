import pandas as pd
from pattern_detector import detect_vcp, detect_meta_pullback
from minervini import minervini_signal


def _jlaw_signal(ticker, df):
    """
    J Law entry: VCP breakout, else pullback to the M.E.T.A. / breakout level.
    Returns a signal dict or None. (Original J Law logic, unchanged.)
    """
    vcp = detect_vcp(df)
    if vcp is None:
        return None
    if vcp['breakout_now']:
        return {
            'ticker': ticker,
            'type': 'BUY_BREAKOUT',
            'price': vcp['close'],
            'stop_loss': vcp['pivot'] * 0.97,
            'reason': f"VCP breakout, pivot={vcp['pivot']:.2f}",
        }
    meta = detect_meta_pullback(df, vcp['pivot'])
    if meta['is_meta']:
        return {
            'ticker': ticker,
            'type': 'BUY_PULLBACK',
            'price': vcp['close'],
            'stop_loss': vcp['pivot'] * 0.97,
            'reason': f"Pullback to BOL, META score={meta['meta_score']}",
        }
    return None


def generate_entry_signals(watchlist, stock_data, minervini_universe=None, rs_ratings=None):
    """
    Dual-strategy BUY signals:

      * J LAW path      - runs on the Stage-2 `watchlist` (VCP breakout / META pullback).
      * MINERVINI path  - runs its own Trend-Template selection over `minervini_universe`
                          (defaults to the full loaded universe) and only fires at a valid
                          VCP pivot buy price.

    Every signal is tagged with `source` / `sources`. When both strategies fire on the
    same ticker it is merged into one high-conviction confluence signal.

    `rs_ratings` is the {ticker: RS rating} map from minervini.compute_rs_ratings().
    """
    rs_ratings = rs_ratings or {}
    by_ticker = {}
    order = []

    # ---- J Law path (Stage-2 watchlist) ----
    for ticker in watchlist:
        df = stock_data.get(ticker)
        if df is None:
            continue
        try:
            sig = _jlaw_signal(ticker, df)
        except Exception as e:                       # isolate a bad ticker; never abort the scan
            print(f"[signal] J Law error on {ticker}: {e}")
            continue
        if sig:
            sig['source'] = 'JLAW'
            sig['strategy'] = 'J Law (Stage 2 / VCP / META)'
            sig['sources'] = ['JLAW']
            by_ticker[ticker] = sig
            order.append(ticker)

    # ---- Minervini path (independent Trend-Template selection) ----
    mrv_universe = minervini_universe if minervini_universe is not None else list(stock_data.keys())
    for ticker in mrv_universe:
        df = stock_data.get(ticker)
        if df is None:
            continue
        try:
            sig = minervini_signal(ticker, df, rs_rating=rs_ratings.get(ticker))
        except Exception as e:                       # isolate a bad ticker; never abort the scan
            print(f"[signal] Minervini error on {ticker}: {e}")
            continue
        if not sig:
            continue
        if ticker in by_ticker:
            # Confluence: both strategies agree -> merge, adopting Minervini's precise
            # buy-point risk levels (stop below the base capped at 8%; measured 3R target).
            existing = by_ticker[ticker]
            existing['sources'] = sorted(set(existing['sources']) | {'MINERVINI'})
            existing['source'] = '+'.join(existing['sources'])
            existing['confluence'] = True
            existing['rs_rating'] = sig.get('rs_rating')
            existing['price'] = sig['price']
            existing['stop_loss'] = sig['stop_loss']
            existing['pivot'] = sig.get('pivot')
            existing['target'] = sig.get('target')
            existing['buy_zone_high'] = sig.get('buy_zone_high')
            existing['risk_pct'] = sig.get('risk_pct')
            existing['actionable_now'] = True
            existing['minervini'] = {
                k: sig[k] for k in ('pivot', 'stop_loss', 'target', 'buy_zone_high', 'risk_pct')
                if k in sig
            }
            existing['reason'] = existing['reason'] + f" | +MINERVINI confluence (RS {sig.get('rs_rating')})"
        else:
            sig['sources'] = ['MINERVINI']
            by_ticker[ticker] = sig
            order.append(ticker)

    # RS Rating is a real, universe-wide computed metric (minervini.compute_rs_ratings),
    # but only the Minervini path attaches it today. Backfill it onto every signal —
    # including JLAW-only ones — so the dashboard can show a real momentum rating
    # instead of a fake confidence number for every pick, not just Minervini's.
    signals = [by_ticker[t] for t in order]
    for sig in signals:
        if sig.get('rs_rating') is None:
            sig['rs_rating'] = rs_ratings.get(sig['ticker'])

    return signals


def generate_exit_signals(positions, stock_data):
    """
    For open positions, check Sell Into Weakness (below 20MA) and simple profit target.
    """
    exits = []
    for pos in positions:
        ticker = pos['ticker']
        df = stock_data.get(ticker)
        if df is None:
            continue
        last = df.iloc[-1]
        close = last['Close']
        # Sell into Weakness: below 20MA
        ma20 = last.get('MA_20')
        if pd.notna(ma20) and close < ma20:
            exits.append({'ticker': ticker, 'type': 'SELL_SIW', 'price': close, 'reason': 'Closed below 20MA'})
            continue
        # Take profit after 20% gain (Sell into Strength simple)
        entry_price = pos['avg_price']
        if close > entry_price * 1.20:
            exits.append({'ticker': ticker, 'type': 'SELL_SIS', 'price': close, 'reason': '20% gain target hit'})
    return exits
