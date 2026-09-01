import pandas as pd
from pattern_detector import detect_vcp, detect_meta_pullback
from minervini import (
    minervini_signal, minervini_watch, TIMING_BUY_NOW, TIMING_BUY_ON_BREAKOUT,
)
from config import MAX_WATCH_ENTRIES


# Maximum structural risk on any J Law entry (Minervini's 8% rule — the same
# cap minervini_signal already enforces). A signal whose volatility-correct
# stop would sit deeper than this is rejected, not clamped: clamping would
# recreate the too-tight-stop problem this function exists to fix.
JLAW_MAX_RISK_PCT = 0.08
# Stop must clear at least this many ATRs of room below entry.
JLAW_ATR_STOP_MULT = 2.0


def _jlaw_stop(pivot, entry, atr=None):
    """Stop for a J Law entry: below the pivot AND outside daily noise.

    STK-05 remediation (forensic audit 2026-07-28): the pullback branch
    used a fixed `pivot * 0.97` regardless of how far the M.E.T.A. pullback
    price had actually moved from the pivot, which could place the stop
    ABOVE the entry (live AMN signal: entry 33.45, stop 34.66). Taking the
    lower of "3% below pivot" and "2% below entry" fixed the inversion.

    STK-08 remediation (alpha post-mortem 2026-09-01): that fix hard-wired
    an at-most-2%-below-entry stop with no relation to the stock's actual
    volatility. Every closed trade in the live book exited at exactly
    -2.00% "Stop Loss" (0 winners / 6 losers): a 2% stop on stocks with
    2-4% daily ranges is inside ordinary noise and has near-certain touch
    probability within days, while the strategy's profit paths (3R target,
    20% gain) need moves it never survives long enough to see. ATR was
    computed in data_loader for exactly this purpose and never used
    anywhere. The stop now also clears JLAW_ATR_STOP_MULT x ATR of room
    below entry; if the volatility-correct stop would exceed
    JLAW_MAX_RISK_PCT of entry, the signal is rejected (return None) --
    the name is too volatile to trade with an acceptable stop, same as
    minervini_signal's own 8% structural-risk rejection.
    """
    stop = min(pivot * 0.97, entry * 0.98)
    if atr is not None and atr == atr and atr > 0:  # atr==atr filters NaN
        stop = min(stop, entry - JLAW_ATR_STOP_MULT * atr)
    if stop <= 0 or (entry - stop) / entry > JLAW_MAX_RISK_PCT:
        return None
    return stop


def _jlaw_signal(ticker, df):
    """
    J Law entry: VCP breakout, else pullback to the M.E.T.A. / breakout level.
    Returns a signal dict or None. (Original J Law logic, unchanged except
    for the stop-loss computation -- see _jlaw_stop.)
    """
    vcp = detect_vcp(df)
    if vcp is None:
        return None
    atr = df.iloc[-1].get('ATR') if 'ATR' in df.columns else None
    if vcp['breakout_now']:
        # STK-08 buy-zone gate (alpha post-mortem 2026-09-01): the breakout
        # branch had no extension check, unlike the Minervini path's
        # pivot*1.05 buy zone (minervini.py). Live consequence: TGT bought
        # 18.5% above its pivot with an 18.2% stop — the classic extended
        # chase this strategy is supposed to forbid. Same 5% zone here.
        if vcp['close'] > vcp['pivot'] * 1.05:
            return None
        stop = _jlaw_stop(vcp['pivot'], vcp['close'], atr=atr)
        if stop is None or stop >= vcp['close']:
            return None  # rejected: inverted stop or risk beyond JLAW_MAX_RISK_PCT
        return {
            'ticker': ticker,
            'type': 'BUY_BREAKOUT',
            'price': vcp['close'],
            'stop_loss': stop,
            'timing': TIMING_BUY_NOW,
            'trigger_price': round(float(vcp['close']), 4),
            'reason': f"VCP breakout, pivot={vcp['pivot']:.2f}",
        }
    meta = detect_meta_pullback(df, vcp['pivot'])
    if meta['is_meta']:
        stop = _jlaw_stop(vcp['pivot'], vcp['close'], atr=atr)
        if stop is None or stop >= vcp['close']:
            return None  # rejected: inverted stop or risk beyond JLAW_MAX_RISK_PCT
        return {
            'ticker': ticker,
            'type': 'BUY_PULLBACK',
            'price': vcp['close'],
            'stop_loss': stop,
            'timing': TIMING_BUY_NOW,
            'trigger_price': round(float(vcp['close']), 4),
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
            existing['trigger_price'] = sig['price']
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


def generate_watch_signals(universe, stock_data, rs_ratings=None, exclude_tickers=None,
                           limit=MAX_WATCH_ENTRIES):
    """WATCH tier: structurally qualified names that are NOT buyable today.

    S3: minervini_signal() drops a name that passes the Trend Template and has a
    tight VCP base purely because of WHERE PRICE IS -- extended above the buy
    zone, structural stop too far, or still coiling under the pivot -- and the
    name then vanished with no record. That left no alert for the buy-zone
    window the strategy actually trades, and nothing to measure
    screened-but-not-traded hit rates against.

    The result is a SEPARATE list and is never merged into the entry signals: a
    watch entry has no 'price' and no 'stop_loss', so it cannot be sized or
    executed, and it can only become a position by later passing
    minervini_signal()'s unchanged hard gates.

    `exclude_tickers` is the set of tickers that already produced an entry
    signal this run -- a name is either actionable or on watch, never both.
    """
    rs_ratings = rs_ratings or {}
    exclude = set(exclude_tickers or ())
    watch = []

    for ticker in universe:
        if ticker in exclude:
            continue
        df = stock_data.get(ticker)
        if df is None:
            continue
        try:
            entry = minervini_watch(ticker, df, rs_rating=rs_ratings.get(ticker))
        except Exception as e:                   # isolate a bad ticker; never abort the scan
            print(f"[watch] Minervini watch error on {ticker}: {e}")
            continue
        if entry:
            watch.append(entry)

    # Closest-to-tradeable first, then by momentum, so a long tail of WAIT FOR
    # BREAKOUT names cannot bury the ones sitting right at their pivot.
    watch.sort(key=lambda w: (w['timing'] != TIMING_BUY_ON_BREAKOUT, -(w.get('rs_rating') or 0)))
    if limit is not None and len(watch) > limit:
        print(f"[watch] {len(watch)} watch candidates -> keeping top {limit} by timing/RS")
        watch = watch[:limit]
    return watch


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
