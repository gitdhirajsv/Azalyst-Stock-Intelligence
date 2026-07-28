"""STK-01 regression: the stop that sized a trade must be the stop that is
persisted with the position.

Forensic audit (2026-07-28): execute_trade() had no stop_loss parameter and
the BUY-path INSERT omitted the stop_loss column (even though the positions
table schema already defines one) -- every open position in the live
database/paper_trades.db had stop_loss=NULL. The tight structural stop that
justified the position's size (via RiskManager.position_size) was thrown
away the instant the trade was booked.
"""

from __future__ import annotations

import sqlite3

import paper_trader


def _fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(paper_trader, "DB_PATH", str(db_path))
    paper_trader.init_db()
    return str(db_path)


def _positions(db_path):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT ticker, shares, avg_price, stop_loss FROM positions"
    ).fetchall()
    conn.close()
    return rows


def test_buy_persists_the_stop_loss(tmp_path, monkeypatch):
    db_path = _fresh_db(tmp_path, monkeypatch)

    ok, msg = paper_trader.execute_trade(
        "RCUS", "BUY", 100, 30.0, reason="Pullback to BOL, META score=3",
        source="JLAW", sector="Healthcare", rs_rating=92, stop_loss=27.83,
    )

    assert ok, msg
    rows = _positions(db_path)
    assert len(rows) == 1
    ticker, shares, avg_price, stop_loss = rows[0]
    assert ticker == "RCUS"
    assert stop_loss == 27.83, (
        "the stop that sized this trade must be persisted with the "
        "position, not dropped at booking"
    )


def test_top_up_keeps_the_original_stop_not_the_new_signal_stop(tmp_path, monkeypatch):
    db_path = _fresh_db(tmp_path, monkeypatch)

    paper_trader.execute_trade(
        "RCUS", "BUY", 100, 30.0, reason="initial", stop_loss=27.83,
    )
    # A later top-up signal computes its OWN (different) stop off the new
    # entry price -- the original position's stop must not be overwritten.
    paper_trader.execute_trade(
        "RCUS", "BUY", 50, 31.5, reason="top-up", stop_loss=29.00,
    )

    rows = _positions(db_path)
    assert len(rows) == 1
    _, shares, avg_price, stop_loss = rows[0]
    assert shares == 150
    assert stop_loss == 27.83, (
        "topping up an existing position must not overwrite its original "
        "stop with the new signal's stop"
    )


def test_top_up_backfills_a_stop_if_the_existing_position_somehow_has_none(tmp_path, monkeypatch):
    """Covers migrated/legacy rows that predate this field."""
    db_path = _fresh_db(tmp_path, monkeypatch)

    paper_trader.execute_trade("RCUS", "BUY", 100, 30.0, reason="initial")  # no stop_loss passed -> NULL
    paper_trader.execute_trade("RCUS", "BUY", 50, 31.5, reason="top-up", stop_loss=29.00)

    rows = _positions(db_path)
    _, _, _, stop_loss = rows[0]
    assert stop_loss == 29.00


def test_sell_does_not_require_a_stop_loss_argument(tmp_path, monkeypatch):
    db_path = _fresh_db(tmp_path, monkeypatch)
    paper_trader.execute_trade("RCUS", "BUY", 100, 30.0, stop_loss=27.83)

    ok, msg = paper_trader.execute_trade("RCUS", "SELL", 100, 27.2, reason="Stop Loss")

    assert ok, msg
    assert _positions(db_path) == []
