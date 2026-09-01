"""STK-08/09/10 regression tests (alpha post-mortem 2026-09-01).

The live book's realized track record was 0 winners / 6 losers, every exit
"Stop Loss" at exactly -2.00%. Root causes covered here:

  STK-09a  Same-bar sell->rebuy loop: RHI was bought and stopped out FOUR
           times at identical prices over one weekend (Aug 29-31), because
           the only re-entry guard (`ticker in held_tickers`) vacated the
           moment the stop-out SELL removed the position, while up to 5
           entry-eligible cron runs evaluated the same stale Friday bar.
           66% of all realized losses came from this one loop.
  STK-09b  Entry-bar low counted against the stop: the fill is the entry
           bar's CLOSE, but the stop engine tested that same bar's LOW —
           price action from before the position existed.
  STK-08   Fixed 2%-below-entry stops with no relation to ATR: guaranteed
           churn on stocks whose daily range exceeds 2%.
  STK-10   "Unclassified" sector (yfinance .info fails from cloud IPs)
           grouped as ONE sector with a 2-position cap, dead-locking the
           whole 8-slot book at 2 positions (74% cash).
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import paper_trader
    paper_trader.init_db()
    return paper_trader


# ---------------------------------------------------------------- STK-09a

def test_last_trade_bar_date_records_and_returns_bar(tmp_db):
    pt = tmp_db
    pt.execute_trade('RHI', 'BUY', 100, 45.31, reason='t', stop_loss=44.40,
                     bar_date='2026-08-28')
    pt.execute_trade('RHI', 'SELL', 100, 44.4038, reason='Stop Loss',
                     bar_date='2026-08-28')
    assert pt.last_trade_bar_date('RHI') == '2026-08-28'


def test_latch_blocks_same_bar_and_allows_new_bar(tmp_db):
    pt = tmp_db
    pt.execute_trade('RHI', 'BUY', 100, 45.31, reason='t', stop_loss=44.40,
                     bar_date='2026-08-28')
    pt.execute_trade('RHI', 'SELL', 100, 44.4038, reason='Stop Loss',
                     bar_date='2026-08-28')
    last = pt.last_trade_bar_date('RHI')
    # Same completed bar -> the azalyst.py entry-loop condition must block.
    assert str(last)[:10] >= '2026-08-28'
    # A NEW bar printed -> tradeable again.
    assert not (str(last)[:10] >= '2026-08-31')


def test_latch_legacy_rows_fall_back_to_run_date(tmp_db):
    """Rows written before the bar_date column existed must fall back to the
    run date, which is >= the bar date — i.e. the fallback can only BLOCK,
    never wrongly allow."""
    pt = tmp_db
    pt.execute_trade('AMN', 'BUY', 10, 34.79, reason='legacy', stop_loss=34.09)
    assert pt.last_trade_bar_date('AMN') is not None


def test_latch_survives_missing_trades_table(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import paper_trader as pt
    # No init_db(): trades table absent. Must return None, not raise.
    assert pt.last_trade_bar_date('XYZ') is None


# ---------------------------------------------------------------- STK-09b

def _bars(index_dates, lows, closes, opens=None):
    idx = pd.to_datetime(index_dates)
    n = len(idx)
    return pd.DataFrame({
        'Open': opens or [c + 0.2 for c in closes],
        'High': [c + 0.7 for c in closes],
        'Low': lows,
        'Close': closes,
        'Volume': [1e6] * n,
    }, index=idx)


def test_stop_not_checked_against_entry_bars_own_low():
    from risk_manager import evaluate_stop_loss_exits
    # Entry bar's low (44.30) already breached the stop (44.40) BEFORE the
    # fill at that bar's close (45.31) — must NOT stop out.
    df = _bars(['2026-08-27', '2026-08-28'], lows=[45.0, 44.30], closes=[45.2, 45.31])
    pos = pd.DataFrame([{'ticker': 'RHI', 'shares': 100, 'avg_price': 45.31,
                         'stop_loss': 44.40, 'entry_bar_date': '2026-08-28'}])
    assert evaluate_stop_loss_exits(pos, {'RHI': df}) == []


def test_stop_fires_normally_from_next_bar_onward():
    from risk_manager import evaluate_stop_loss_exits
    df = _bars(['2026-08-28', '2026-08-31'], lows=[44.30, 44.10], closes=[45.31, 44.5])
    pos = pd.DataFrame([{'ticker': 'RHI', 'shares': 100, 'avg_price': 45.31,
                         'stop_loss': 44.40, 'entry_bar_date': '2026-08-28'}])
    exits = evaluate_stop_loss_exits(pos, {'RHI': df})
    assert len(exits) == 1 and exits[0][3] == 'Stop Loss'


def test_legacy_position_without_entry_bar_date_still_protected():
    from risk_manager import evaluate_stop_loss_exits
    df = _bars(['2026-08-27', '2026-08-28'], lows=[45.0, 44.30], closes=[45.2, 45.31])
    pos = pd.DataFrame([{'ticker': 'RHI', 'shares': 100, 'avg_price': 45.31,
                         'stop_loss': 44.40, 'entry_bar_date': None}])
    assert len(evaluate_stop_loss_exits(pos, {'RHI': df})) == 1


# ---------------------------------------------------------------- STK-08

def test_jlaw_stop_clears_two_atr():
    from signal_generator import _jlaw_stop, JLAW_ATR_STOP_MULT
    entry, pivot, atr = 100.0, 100.5, 1.5  # 1.5% daily ATR
    stop = _jlaw_stop(pivot, entry, atr=atr)
    assert stop is not None
    assert entry - stop >= JLAW_ATR_STOP_MULT * atr - 1e-9


def test_jlaw_stop_rejects_too_volatile_names():
    from signal_generator import _jlaw_stop
    # 2 x ATR = 10% of entry > JLAW_MAX_RISK_PCT (8%): reject, don't clamp.
    assert _jlaw_stop(100.5, 100.0, atr=5.0) is None


def test_jlaw_stop_without_atr_keeps_legacy_bound():
    from signal_generator import _jlaw_stop
    # No ATR available: lower of pivot*0.97 and entry*0.98 (legacy STK-05 rule).
    assert _jlaw_stop(102.0, 100.0, atr=None) == pytest.approx(98.0)
    assert _jlaw_stop(100.5, 100.0, atr=None) == pytest.approx(97.485)


# ---------------------------------------------------------------- STK-10

def test_unclassified_sector_exempt_from_per_sector_cap():
    from risk_manager import check_diversification
    book = [{'ticker': 'A', 'sector': 'Unclassified'},
            {'ticker': 'B', 'sector': 'Unclassified'}]
    ok, reason = check_diversification(book, 'Unclassified')
    assert ok, reason


def test_real_sector_cap_still_enforced():
    from risk_manager import check_diversification
    book = [{'ticker': 'A', 'sector': 'Healthcare / Biotechnology'},
            {'ticker': 'B', 'sector': 'Healthcare / Healthcare Plans'}]
    ok, reason = check_diversification(book, 'Healthcare / Diagnostics')
    assert not ok and 'Healthcare' in reason


def test_max_open_positions_still_caps_unclassified_book():
    from risk_manager import check_diversification
    from config import MAX_OPEN_POSITIONS
    book = [{'ticker': f'T{i}', 'sector': 'Unclassified'}
            for i in range(MAX_OPEN_POSITIONS)]
    ok, _ = check_diversification(book, 'Unclassified')
    assert not ok
