"""STK-04 regression: total_risk_ok must actually work (it had zero call
sites and a DataFrame-iteration bug), and the entry path must cap position
count and per-sector concentration.

Forensic audit (2026-07-28): no aggregate risk check, no position-count
limit, no sector cap existed anywhere -- the live book ended up with 3 of
4 positions in healthcare/biotech by accident, each ~25% of equity.
"""

from __future__ import annotations

import pandas as pd
import pytest

from risk_manager import RiskManager, check_diversification
import config


def _df(records):
    return pd.DataFrame(records)


def _day(close):
    return pd.DataFrame({"Open": [close], "High": [close], "Low": [close], "Close": [close]})


# ── total_risk_ok ────────────────────────────────────────────────────────────

def test_total_risk_ok_accepts_dataframe_records_not_column_names():
    """The pre-fix bug: `for pos in open_positions` over a DataFrame
    iterates column NAMES (strings), not rows. Calling it the way the real
    pipeline would (via .to_dict('records')) must work correctly."""
    equity = 100_000.0
    rm = RiskManager(equity)
    positions = _df([
        {"ticker": "AAA", "shares": 100, "avg_price": 50.0, "stop_loss": 48.0},
    ]).to_dict("records")
    stock_data = {"AAA": _day(50.0)}

    # risk_per_share = 50-48=2, total_risk = 200, well under 6% of 100k (6000)
    assert rm.total_risk_ok(positions, stock_data) is True


def test_total_risk_ok_false_when_aggregate_risk_exceeds_cap():
    equity = 100_000.0
    rm = RiskManager(equity)
    # 3 positions each risking ~2500 (7.5% total) vs a 6% (6000) cap.
    positions = [
        {"ticker": "AAA", "shares": 500, "avg_price": 50.0, "stop_loss": 45.0},   # risk 2500
        {"ticker": "BBB", "shares": 500, "avg_price": 50.0, "stop_loss": 45.0},   # risk 2500
        {"ticker": "CCC", "shares": 500, "avg_price": 50.0, "stop_loss": 45.0},   # risk 2500
    ]
    stock_data = {t: _day(50.0) for t in ("AAA", "BBB", "CCC")}

    assert rm.total_risk_ok(positions, stock_data) is False


def test_total_risk_ok_none_stop_falls_back_to_5pct_not_silently_zero():
    """pos.get('stop_loss', default) never fires when the key exists with
    value None (which it always does since STK-01 added the column) --
    must check `is None` explicitly, not rely on dict.get's default."""
    equity = 100_000.0
    rm = RiskManager(equity)
    positions = [{"ticker": "AAA", "shares": 1000, "avg_price": 50.0, "stop_loss": None}]
    stock_data = {"AAA": _day(50.0)}

    # 5% fallback: risk_per_share = 50*0.05 = 2.5, total_risk = 2500 < 6000 -> True
    assert rm.total_risk_ok(positions, stock_data) is True

    # Scale up shares so the 5% fallback risk clearly breaches the cap.
    positions[0]["shares"] = 3000  # total_risk = 7500 > 6000
    assert rm.total_risk_ok(positions, stock_data) is False


def test_total_risk_ok_ignores_ticker_with_no_market_data():
    rm = RiskManager(100_000.0)
    positions = [{"ticker": "UNKNOWN", "shares": 100, "avg_price": 50.0, "stop_loss": 45.0}]
    assert rm.total_risk_ok(positions, {}) is True


def test_total_risk_ok_empty_book_is_ok():
    rm = RiskManager(100_000.0)
    assert rm.total_risk_ok([], {}) is True


# ── config caps exist and are sane ──────────────────────────────────────────

def test_diversification_config_constants_exist():
    assert config.MAX_OPEN_POSITIONS >= 1
    assert config.MAX_POSITIONS_PER_SECTOR >= 1
    assert config.MAX_POSITIONS_PER_SECTOR < config.MAX_OPEN_POSITIONS


# ── check_diversification (the actual function azalyst.py's entry loop calls) ─

def test_sector_cap_blocks_a_third_same_sector_entry():
    """Reconstructs the exact live scenario: PACS + CNC + ROIV + KYMR, 3 of
    4 in healthcare/biotech. The 3rd healthcare entry must be blocked."""
    combined = [
        {"ticker": "PACS", "sector": "Unclassified"},
        {"ticker": "CNC", "sector": "Healthcare / Healthcare Plans"},
        {"ticker": "ROIV", "sector": "Healthcare / Biotechnology"},
    ]
    ok, reason = check_diversification(combined, "Healthcare / Biotechnology")
    assert ok is False
    assert "sector" in reason


def test_sector_cap_allows_the_second_entry_in_a_sector():
    combined = [{"ticker": "CNC", "sector": "Healthcare / Healthcare Plans"}]
    ok, reason = check_diversification(combined, "Healthcare / Healthcare Plans")
    assert ok is True


def test_sector_cap_allows_a_different_sector_even_at_cap():
    combined = [
        {"ticker": "CNC", "sector": "Healthcare"},
        {"ticker": "ROIV", "sector": "Healthcare"},
    ]
    ok, reason = check_diversification(combined, "Technology")
    assert ok is True


def test_position_count_cap_blocks_beyond_max_open():
    combined = [{"ticker": f"T{i}", "sector": f"S{i}"} for i in range(config.MAX_OPEN_POSITIONS)]
    ok, reason = check_diversification(combined, "Some New Sector")
    assert ok is False
    assert "max open positions" in reason


def test_position_count_cap_allows_entry_below_max():
    combined = [{"ticker": f"T{i}", "sector": f"S{i}"} for i in range(config.MAX_OPEN_POSITIONS - 1)]
    ok, reason = check_diversification(combined, "Some New Sector")
    assert ok is True
