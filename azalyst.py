import os
import pandas as pd
from datetime import datetime, timezone
from config import *
from universe import get_universe
from data_loader import load_stock_data
from utils import fetch_historical, compute_moving_averages, compute_volume_ma, fetch_sector
from market_regime import detect_bull_regime
from stock_screener import apply_stage2_screen
from signal_generator import generate_entry_signals, generate_watch_signals
from minervini import compute_rs_ratings
from fundamentals import apply_fundamental_filter
from risk_manager import RiskManager, evaluate_stop_loss_exits, check_diversification
from paper_trader import (
    init_db, get_cash, get_positions, execute_trade, record_equity_snapshot,
    last_trade_bar_date,
)
from performance import generate_performance_report
from signal_history import HISTORY_PATH, append_observations

# STK-07 remediation (forensic audit 2026-07-28): the cron ran this pipeline
# hourly during market hours (14:30-21:30 UTC) using yfinance's 1d-interval
# bars. Mid-session, "the last bar" is today's still-forming candle -- a
# breakout "confirmed" at 10:30am ET can fail by the close, and the
# >=1.4x-average-volume gate is measured against a partial day's volume, not
# the full session. Entries (new positions) are now evaluated only outside
# this window, i.e. against a completed daily bar; stop-loss monitoring
# (evaluate_stop_loss_exits) still runs every cycle regardless -- an open
# risk should be checked as often as possible, only NEW risk needs a
# finished bar. 13-20 UTC is a deliberately conservative superset of the
# actual ~13:30-21:00 UTC session (both EDT and EST close times), so the
# gate errs toward skipping a borderline hour rather than trading on a
# partial one.
US_MARKET_ACTIVE_UTC_HOURS = range(13, 21)


def _entries_allowed(now=None):
    """True only when it's safe to treat the latest daily bar as complete.

    STK-09 addition (alpha post-mortem 2026-09-01): weekend runs are also
    excluded. On Sat/Sun the "latest bar" is Friday's, which the Friday
    after-hours runs already evaluated; weekend entry passes added nothing
    but extra chances to re-fire the same stale signal (the RHI weekend
    rebuy loop). The per-bar trade latch below is the hard guard — this is
    belt-and-suspenders that also saves pointless weekend scans.
    """
    now = now or datetime.now(timezone.utc)
    return now.weekday() < 5 and now.hour not in US_MARKET_ACTIVE_UTC_HOURS


def _latest_bar_date(df):
    """ISO date of a ticker's most recent completed daily bar, or None."""
    try:
        return str(pd.Timestamp(df.index[-1]).date())
    except Exception:
        return None


def run_pipeline():
    print(f"[{datetime.now().isoformat()}] Starting Azalyst Stock Intelligence Pipeline...")
    init_db()

    # Load Universe
    tickers = get_universe()
    print(f"Loaded {len(tickers)} tickers from universe.")

    # Load Data
    stock_data, _ = load_stock_data(tickers)
    
    # Market Regime
    benchmark_df = fetch_historical(BENCHMARK_TICKER, "2y")
    if benchmark_df is None or benchmark_df.empty:
        print("Failed to fetch benchmark data.")
        return

    # Regime detection needs the moving averages on the benchmark (golden cross,
    # 200MA slope). Without this the MAs are missing and the regime is stuck BEAR.
    benchmark_df = compute_moving_averages(benchmark_df)
    benchmark_df = compute_volume_ma(benchmark_df)

    regime = detect_bull_regime(benchmark_df)
    is_bull = regime.get('is_bull', False)
    print(f"Market Regime: {'BULL' if is_bull else 'BEAR'}")
    
    # Relative-strength ratings across the whole universe (needed by BOTH paths).
    rs_ratings = compute_rs_ratings(stock_data)

    # Screener (J Law Stage-2 structure).
    stage2_passed = apply_stage2_screen(stock_data)
    print(f"Tickers passing Stage 2: {len(stage2_passed)}")

    # Enforce the RS Rating gate on the J Law watchlist too (Minervini's watershed
    # is 89). The Minervini path already gates on RS inside its Trend Template.
    before_rs = len(stage2_passed)
    stage2_passed = [t for t in stage2_passed if rs_ratings.get(t, 0) >= RS_RATING_MIN]
    print(f"After RS Rating >= {RS_RATING_MIN} gate: {len(stage2_passed)} (was {before_rs})")

    # Dual-strategy signals: J Law (Stage-2 watchlist) + Minervini (full universe,
    # own Trend-Template selection, fires only at a valid VCP pivot buy price).
    signals = generate_entry_signals(
        stage2_passed, stock_data,
        minervini_universe=list(stock_data.keys()),
        rs_ratings=rs_ratings,
    )
    n_jlaw = sum(1 for s in signals if 'JLAW' in s.get('sources', []))
    n_mrv = sum(1 for s in signals if 'MINERVINI' in s.get('sources', []))
    n_conf = sum(1 for s in signals if s.get('confluence'))
    print(f"Entry Signals Generated: {len(signals)} "
          f"(J Law: {n_jlaw}, Minervini: {n_mrv}, confluence: {n_conf})")

    # Fundamental layer (revenue, not profit): last-quarter YoY revenue growth > 5%
    # and accelerating. Applied only to the small actionable signal list (cheap), and
    # dropping only stocks that fail on VERIFIED data -- unverifiable ones are flagged,
    # not silently hidden.
    signals = apply_fundamental_filter(signals, require_when_unverified=REQUIRE_VERIFIED_FUNDAMENTALS)

    # Persist signals immediately so the dashboard reflects them even if the
    # execution step below raises.
    import json
    with open("signals.json", "w") as f:
        json.dump(signals, f, indent=2, default=str)

    # WATCH tier (S3). minervini_signal() rejects a structurally qualified name
    # purely on WHERE PRICE IS (extended past pivot*1.05, structural stop > 8%,
    # or still under the pivot) and the name then vanished with no record --
    # killing the buy-zone alert window and any hit-rate analysis. These are
    # kept on a SEPARATE list with an explicit numeric trigger_price. They are
    # never appended to `signals` and never enter the execution loop below: a
    # watch name can only be bought by later passing the same unchanged gates.
    # Wrapped in try/except: this is an observability feature, not part of the
    # trading path, so a failure here must never abort stop-loss exits or
    # entries below it.
    watch = []
    try:
        watch = generate_watch_signals(
            list(stock_data.keys()), stock_data,
            rs_ratings=rs_ratings,
            exclude_tickers={s['ticker'] for s in signals},
        )
        print(f"Watch entries (not buyable at today's price): {len(watch)}")
        with open("watchlist.json", "w") as f:
            json.dump(watch, f, indent=2, default=str)
    except Exception as e:
        print(f"[watch] could not generate watch list: {e}")

    # signals.json is rewritten every cycle (mode 'w', above) because the
    # dashboard consumes it as the CURRENT actionable list. The durable record
    # lives here instead -- append-only, so an empty scan can no longer erase
    # what the engine saw yesterday.
    try:
        n_recorded = append_observations(signals, watch)
        print(f"[history] recorded {n_recorded} new observation(s) -> {HISTORY_PATH}")
    except Exception as e:
        print(f"[history] could not record signal history: {e}")

    # Trading Logic
    cash = get_cash()
    positions = get_positions()
    current_equity = cash
    
    # Calculate current equity including open positions
    if not positions.empty:
        for idx, pos in positions.iterrows():
            ticker = pos['ticker']
            shares = pos['shares']
            if ticker in stock_data:
                current_price = stock_data[ticker]['Close'].iloc[-1]
                current_equity += shares * current_price
    
    rm = RiskManager(current_equity)
    
    # Evaluate Exits (Stop Loss) -- see risk_manager.evaluate_stop_loss_exits
    # (STK-02 remediation, forensic audit 2026-07-28) for why this replaced
    # the old hardcoded "Dummy exit rule" checked against Close only.
    for ticker, shares, fill_price, reason in evaluate_stop_loss_exits(positions, stock_data):
        exit_bar = _latest_bar_date(stock_data[ticker]) if ticker in stock_data else None
        execute_trade(ticker, 'SELL', shares, fill_price, reason=reason, bar_date=exit_bar)
        print(f"SELL ({reason}) {shares} {ticker} @ {fill_price}")
    
    # Execute Entries
    # Only buy if in Bull Regime and we have signals
    #
    # STK-04 remediation (forensic audit 2026-07-28): sector used to be
    # fetched (fetch_sector) only AFTER rm.check_entry already approved the
    # trade -- pure dashboard metadata, never an input to any decision. And
    # total_risk_ok existed but was never called from here at all. Combined
    # with 25%-notional sizing (see STK-03), the live book ended up with 3
    # of 4 positions in healthcare/biotech by accident, with no aggregate
    # risk check of any kind. `existing_positions` is the DB snapshot at
    # scan start; `positions_this_cycle` tracks buys already executed
    # earlier in THIS scan (positions/get_positions() is not re-queried
    # mid-loop), so the position-count/sector/total-risk gates below see the
    # book as it actually stands after each buy, not a stale start-of-cycle
    # snapshot.
    existing_positions = positions.to_dict('records') if not positions.empty else []
    positions_this_cycle = []

    entries_allowed = _entries_allowed()
    if not entries_allowed:
        print("[entries] skipped this cycle - within market hours, "
              "today's daily bar is not yet complete (STK-07)")

    if entries_allowed and is_bull and signals:
        for sig in signals:
            try:
                ticker = sig['ticker']
                price = sig['price']

                combined = existing_positions + positions_this_cycle
                held_tickers = {p['ticker'] for p in combined}
                if ticker in held_tickers:
                    continue

                # STK-09 per-bar trade latch (alpha post-mortem 2026-09-01):
                # `ticker in held_tickers` alone vacates the moment a stop-out
                # SELL removes the position, so the same still-valid signal on
                # the same completed bar produced a buy->stop->rebuy loop (RHI
                # was bought and stopped out 4x at identical prices over one
                # weekend, -2% each = 66% of all realized losses). One action
                # per ticker per completed bar: if the ticker's latest recorded
                # trade was decided on (or after) the bar we're looking at now,
                # skip it — a NEW bar must print before it's tradeable again.
                bar_date = _latest_bar_date(stock_data.get(ticker)) if ticker in stock_data else None
                last_bar = last_trade_bar_date(ticker)
                if bar_date and last_bar and str(last_bar)[:10] >= bar_date:
                    print(f"[latch] {ticker} skipped - already traded on bar {last_bar} "
                          f"(current bar {bar_date})")
                    continue

                allowed, shares, reason = rm.check_entry(price, sig['stop_loss'])
                if not allowed:
                    continue

                sector = fetch_sector(ticker)
                div_ok, div_reason = check_diversification(combined, sector)
                if not div_ok:
                    print(f"[risk] {ticker} skipped - {div_reason}")
                    continue

                projected_position = {
                    'ticker': ticker, 'shares': shares, 'avg_price': price,
                    'stop_loss': sig['stop_loss'], 'sector': sector,
                }
                if not rm.total_risk_ok(combined + [projected_position], stock_data):
                    print(f"[risk] {ticker} skipped - total open risk would exceed "
                          f"{MAX_PORTFOLIO_RISK_PCT:.0%} of equity")
                    continue

                source_label = sig.get('source') or '+'.join(sig.get('sources', ['JLAW']))
                success, msg = execute_trade(
                    ticker, 'BUY', shares, price, reason=sig['reason'],
                    source=source_label, sector=sector, rs_rating=sig.get('rs_rating'),
                    stop_loss=sig['stop_loss'], bar_date=bar_date,
                )
                if success:
                    print(f"BUY {shares} {ticker} @ {price} ({sig['reason']})")
                    positions_this_cycle.append(projected_position)
                    # Update RM equity
                    rm.update_equity(get_cash()) # Approximation
            except Exception as e:
                print(f"[execute] error on {sig.get('ticker')}: {e}")
                continue

    # Record equity snapshot for performance analytics (equity curve tracking).
    positions_value = 0.0
    updated_cash = get_cash()
    updated_positions = get_positions()
    if not updated_positions.empty:
        for idx, pos in updated_positions.iterrows():
            t = pos['ticker']
            s = pos['shares']
            if t in stock_data:
                positions_value += s * stock_data[t]['Close'].iloc[-1]
    record_equity_snapshot(updated_cash, positions_value)

    # Performance report
    try:
        report = generate_performance_report()
        if report.get('has_data'):
            em = report.get('equity_metrics', {})
            rm_metrics = report.get('risk_metrics', {})
            tm = report.get('trade_metrics', {})
            print(f"\n--- Performance Report ---")
            if em:
                print(f"  Sharpe: {em.get('sharpe_ratio', 'n/a')}  "
                      f"Sortino: {em.get('sortino_ratio', 'n/a')}  "
                      f"Max DD: {em.get('max_drawdown_pct', 'n/a')}%")
            if rm_metrics:
                print(f"  VaR(95%): {rm_metrics.get('var_95_daily_pct', 'n/a')}%  "
                      f"CVaR(95%): {rm_metrics.get('cvar_95_daily_pct', 'n/a')}%")
            if tm:
                print(f"  Win Rate: {tm.get('win_rate_pct', 'n/a')}%  "
                      f"Profit Factor: {tm.get('profit_factor', 'n/a')}  "
                      f"Avg R: {tm.get('avg_r_multiple', 'n/a')}")
            print(f"---\n")
    except Exception as e:
        print(f"[perf] could not generate performance report: {e}")

    print(f"[{datetime.now().isoformat()}] Pipeline execution complete.")

if __name__ == "__main__":
    run_pipeline()
