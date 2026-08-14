import pandas as pd
from pattern_detector import detect_vcp, detect_meta_pullback
from minervini import minervini_signal


def _jlaw_stop(pivot, entry):
    """Stop for a J Law entry: 3% below the pivot, but never above (or too
    close to) the actual entry price.

    STK-05 remediation (forensic audit 2026-07-28): the pullback branch
    used a fixed `pivot * 0.97` regardless of how far the M.E.T.A. pullback
    price had actually moved from the pivot. detect_meta_pullback only
    requires 2 of 4 "edges" (BOL-zone proximity is just one of them), so a
    pullback can qualify via MA support + volume contraction alone while
    sitting well below the pivot -- detect_vcp allows contractions up to
    10% below pivot. When that happens, pivot*0.97 (3% below pivot) sits
    ABOVE the entry price: a long position sized off a stop that's already
    been breached before the trade is even opened. This was live: a signal
    for AMN priced entry at 33.45 with stop_loss 34.66 (pivot*0.97, 3.6%
    ABOVE entry). Taking the tighter-normally / safer-when-degenerate of
    "3% below pivot" and "2% below actual entry" fixes both cases: when
    entry is near the pivot (the intended case), pivot*0.97 is naturally
    the lower (and wins); when entry has drifted well below the pivot, the
    2%-below-entry level is the lower one and wins, keeping the stop
    correctly beneath the price actually being paid.
    """
    return min(pivot * 0.97, entry * 0.98)


def _jlaw_signal(ticker, df):
    """
    J Law entry: VCP breakout, else pullback to the M.E.T.A. / breakout level.
    Returns a signal dict or None. (Original J Law logic, unchanged except
    for the stop-loss computation -- see _jlaw_stop.)
    """
    vcp = detect_vcp(df)
    if vcp is None:
        return None
    if vcp['breakout_now']:
        stop = _jlaw_stop(vcp['pivot'], vcp['close'])
        if stop >= vcp['close']:
            return None  # defensive: never emit an inverted/non-positive-risk stop
        return {
            'ticker': ticker,
            'type': 'BUY_BREAKOUT',
            'price': vcp['close'],
            'stop_loss': stop,
            'reason': f"VCP breakout, pivot={vcp['pivot']:.2f}",
        }
    meta = detect_meta_pullback(df, vcp['pivot'])
    if meta['is_meta']:
        stop = _jlaw_stop(vcp['pivot'], vcp['close'])
        if stop >= vcp['close']:
            return None  # defensive: never emit an inverted/non-positive-risk stop
        return {
            'ticker': ticker,
            'type': 'BUY_PULLBACK',
            'price': vcp['close'],
            'stop_loss': stop,
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

    # STK-05 remediation (forensic audit 2026-07-28): blanket validation
    # layer, independent of and in addition to the per-path fixes above
    # (_jlaw_stop, minervini_signal's own `if stop >= entry: return None`).
    # No long signal may ever reach the risk manager / paper trader with a
    # stop at or above its entry price -- reject and log instead of letting
    # a bad stop (from either strategy, or from the confluence merge that
    # can overwrite one path's stop with the other's) size a position via
    # RiskManager.position_size's abs(entry - stop) (see STK-03, which
    # also independently rejects this at sizing time -- this is
    # defense-in-depth at the source).
    validated = []
    for sig in signals:
        stop = sig.get('stop_loss')
        price = sig.get('price')
        if stop is not None and price is not None and stop >= price:
            print(f"[signal] rejected {sig.get('ticker')}: stop_loss {stop} >= price {price}")
            continue
        validated.append(sig)

    # ---- RSI / MACD confirmation filters (Murphy, Technical Analysis) ----
    # RSI > 80: overbought — breakout is likely extended, higher reversal risk.
    # MACD < Signal: momentum is not aligned with the buy, lower probability.
    confirmed = []
    for sig in validated:
        ticker = sig.get('ticker')
        df = stock_data.get(ticker)
        if df is not None and len(df) > 0:
            last = df.iloc[-1]
            rsi = last.get('RSI')
            macd = last.get('MACD')
            macd_signal = last.get('MACD_Signal')
            if pd.notna(rsi) and rsi > 80:
                print(f"[signal] filtered {ticker}: RSI={rsi:.1f} > 80 (overbought)")
                sig['filtered_reason'] = f'RSI overbought ({rsi:.1f})'
                continue
            if pd.notna(macd) and pd.notna(macd_signal) and macd < macd_signal:
                print(f"[signal] filtered {ticker}: MACD below signal (bearish momentum)")
                sig['filtered_reason'] = 'MACD bearish crossover'
                continue
        confirmed.append(sig)

    return confirmed


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
