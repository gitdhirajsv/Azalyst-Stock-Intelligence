"""STK-06 regression: the dashboard must show realized losses, not a
hardcoded "$0.00" that never gets recomputed.

Forensic audit (2026-07-28): status.json hardcoded realised_str to
"$0.00" (generate_dashboard.py, prior to this fix) and never recomputed
it, and track_record / closed_trades_list stayed at their zeroed initial
values forever -- even though database/paper_trades.db contains a closed
losing trade (RCUS: BUY 826 @ 30.25 on 2026-07-07, SELL 826 @ 27.20 on
2026-07-16, "Stop Loss") that the public dashboard reported as exactly
zero everywhere a viewer would check: realized P&L, win rate, closed-trade
list, best/worst.
"""

from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from generate_dashboard import compute_realized_trades, build_track_record
import config


def _trades_df(rows):
    return pd.DataFrame(rows)


# ── compute_realized_trades: FIFO matching ──────────────────────────────────

def test_simple_buy_then_sell_computes_realized_pnl():
    trades = _trades_df([
        {"id": 1, "ticker": "RCUS", "action": "BUY", "shares": 826, "price": 30.25, "date": "2026-07-07", "reason": ""},
        {"id": 5, "ticker": "RCUS", "action": "SELL", "shares": 826, "price": 27.200000762939453, "date": "2026-07-16", "reason": "Stop Loss"},
    ])

    closed = compute_realized_trades(trades)

    assert len(closed) == 1
    t = closed[0]
    assert t["ticker"] == "RCUS"
    assert t["realised_pnl"] == pytest.approx(-2519.30, abs=0.01)
    assert t["realised_pnl_pct"] == pytest.approx(-10.08, abs=0.01)
    assert t["reason"] == "Stop Loss"


def test_partial_sell_leaves_remaining_lot_open():
    trades = _trades_df([
        {"id": 1, "ticker": "AAA", "action": "BUY", "shares": 100, "price": 50.0, "date": "2026-01-01", "reason": ""},
        {"id": 2, "ticker": "AAA", "action": "SELL", "shares": 40, "price": 55.0, "date": "2026-01-05", "reason": "partial"},
    ])
    closed = compute_realized_trades(trades)
    assert len(closed) == 1
    assert closed[0]["shares"] == 40
    assert closed[0]["realised_pnl"] == pytest.approx((55.0 - 50.0) * 40)


def test_sell_spanning_two_buy_lots_uses_weighted_average_cost():
    trades = _trades_df([
        {"id": 1, "ticker": "AAA", "action": "BUY", "shares": 50, "price": 40.0, "date": "2026-01-01", "reason": ""},
        {"id": 2, "ticker": "AAA", "action": "BUY", "shares": 50, "price": 60.0, "date": "2026-01-02", "reason": ""},
        {"id": 3, "ticker": "AAA", "action": "SELL", "shares": 100, "price": 55.0, "date": "2026-01-10", "reason": ""},
    ])
    closed = compute_realized_trades(trades)
    assert len(closed) == 1
    t = closed[0]
    # cost basis = 50*40 + 50*60 = 5000; proceeds = 100*55 = 5500; pnl = 500
    assert t["shares"] == 100
    assert t["entry_price"] == pytest.approx(50.0)
    assert t["realised_pnl"] == pytest.approx(500.0)


def test_multiple_tickers_do_not_cross_contaminate():
    trades = _trades_df([
        {"id": 1, "ticker": "AAA", "action": "BUY", "shares": 10, "price": 10.0, "date": "d", "reason": ""},
        {"id": 2, "ticker": "BBB", "action": "BUY", "shares": 20, "price": 20.0, "date": "d", "reason": ""},
        {"id": 3, "ticker": "AAA", "action": "SELL", "shares": 10, "price": 12.0, "date": "d", "reason": ""},
        {"id": 4, "ticker": "BBB", "action": "SELL", "shares": 20, "price": 18.0, "date": "d", "reason": ""},
    ])
    closed = compute_realized_trades(trades)
    by_ticker = {t["ticker"]: t for t in closed}
    assert by_ticker["AAA"]["realised_pnl"] == pytest.approx(20.0)
    assert by_ticker["BBB"]["realised_pnl"] == pytest.approx(-40.0)


def test_no_trades_returns_empty():
    assert compute_realized_trades(pd.DataFrame()) == []
    assert compute_realized_trades(None) == []


def test_sell_exceeding_tracked_lots_does_not_fabricate_a_cost_basis():
    """A SELL with no matching BUY in this table (e.g. a legacy position)
    should simply produce no closed-trade row, not crash or invent a
    cost basis of zero."""
    trades = _trades_df([
        {"id": 1, "ticker": "LEGACY", "action": "SELL", "shares": 10, "price": 100.0, "date": "d", "reason": ""},
    ])
    assert compute_realized_trades(trades) == []


# ── build_track_record ───────────────────────────────────────────────────────

def test_track_record_reflects_a_single_loser():
    closed = [{
        "ticker": "RCUS", "shares": 826, "entry_price": 30.25, "exit_price": 27.2,
        "realised_pnl": -2519.30, "realised_pnl_pct": -10.08, "exit_date": "2026-07-16",
        "reason": "Stop Loss",
    }]
    track = build_track_record(closed)

    assert track["total_trades"] == 1
    assert track["winners"] == 0
    assert track["losers"] == 1
    assert track["win_rate"] == 0.0
    assert track["worst"]["ticker"] == "RCUS"
    assert track["worst"]["pnl_pct"] == pytest.approx(-10.08)


def test_track_record_empty_when_no_closed_trades():
    track = build_track_record([])
    assert track["total_trades"] == 0
    assert track["best"] is None
    assert track["worst"] is None


# ── golden-file test against the actual live database ──────────────────────

def test_live_database_rcus_loss_is_correctly_surfaced():
    """Reproduces the exact live bug report: reads database/paper_trades.db
    directly and confirms the RCUS loss is no longer invisible."""
    conn = sqlite3.connect(config.DB_PATH)
    trades = pd.read_sql("SELECT * FROM trades", conn)
    conn.close()

    closed = compute_realized_trades(trades)
    rcus = [t for t in closed if t["ticker"] == "RCUS"]

    assert len(rcus) == 1, "the live database must contain exactly one closed RCUS trade"
    assert rcus[0]["realised_pnl"] == pytest.approx(-2519.30, abs=0.01)
    assert rcus[0]["realised_pnl_pct"] == pytest.approx(-10.08, abs=0.01)

    track = build_track_record(closed)
    assert track["total_trades"] == 1
    assert track["losers"] == 1
    assert track["winners"] == 0
    assert track["worst"]["ticker"] == "RCUS"
