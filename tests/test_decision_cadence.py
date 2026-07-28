"""STK-07 regression: entries must only be evaluated against a completed
daily bar, never a partial intraday one; stop-loss monitoring must still
run every cycle regardless.

Forensic audit (2026-07-28): the cron ran this pipeline hourly during
market hours (14:30-21:30 UTC) using yfinance's 1d-interval bars. Mid-
session, "the last bar" is today's still-forming candle -- a breakout
"confirmed" at 10:30am ET can fail by the close, and the >=1.4x-average-
volume gate is measured against a partial day's volume, not the full
session.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import azalyst


# ── _entries_allowed in isolation ───────────────────────────────────────────

@pytest.mark.parametrize("hour", [13, 14, 15, 16, 17, 18, 19, 20])
def test_entries_blocked_during_market_hours(hour):
    now = datetime(2026, 7, 28, hour, 0, tzinfo=timezone.utc)
    assert azalyst._entries_allowed(now) is False


@pytest.mark.parametrize("hour", [21, 22, 23, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])
def test_entries_allowed_outside_market_hours(hour):
    now = datetime(2026, 7, 28, hour, 0, tzinfo=timezone.utc)
    assert azalyst._entries_allowed(now) is True


# ── wired into run_pipeline: entries gated, exits are not ───────────────────

def _mock_stock_df():
    idx = pd.bdate_range(end="2026-07-28", periods=5)
    return pd.DataFrame({
        "Open": [100.0] * 5, "High": [101.0] * 5, "Low": [99.0] * 5, "Close": [100.0] * 5,
        "Volume": [1_000_000] * 5,
    }, index=idx)


def _run_pipeline_with(entries_allowed, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # signals.json write must not touch the real repo

    stock_data = {"AAA": _mock_stock_df()}
    signal = {
        "ticker": "AAA", "type": "BUY_PULLBACK", "price": 100.0, "stop_loss": 95.0,
        "reason": "synthetic", "source": "JLAW", "sources": ["JLAW"], "rs_rating": 92,
    }

    with patch("azalyst._entries_allowed", return_value=entries_allowed), \
         patch("azalyst.init_db"), \
         patch("azalyst.get_universe", return_value=["AAA"]), \
         patch("azalyst.load_stock_data", return_value=(stock_data, None)), \
         patch("azalyst.fetch_historical", return_value=_mock_stock_df()), \
         patch("azalyst.compute_moving_averages", side_effect=lambda df, *a, **k: df), \
         patch("azalyst.compute_volume_ma", side_effect=lambda df, *a, **k: df), \
         patch("azalyst.detect_bull_regime", return_value={"is_bull": True}), \
         patch("azalyst.compute_rs_ratings", return_value={"AAA": 95}), \
         patch("azalyst.apply_stage2_screen", return_value=["AAA"]), \
         patch("azalyst.generate_entry_signals", return_value=[signal]), \
         patch("azalyst.apply_fundamental_filter", side_effect=lambda sigs, **k: sigs), \
         patch("azalyst.get_cash", return_value=100_000.0), \
         patch("azalyst.get_positions", return_value=pd.DataFrame()), \
         patch("azalyst.fetch_sector", return_value="Technology"), \
         patch("azalyst.execute_trade", return_value=(True, "Success")) as mock_execute:
        azalyst.run_pipeline()

    return mock_execute


def test_no_buy_executed_when_entries_not_allowed(tmp_path, monkeypatch):
    mock_execute = _run_pipeline_with(entries_allowed=False, tmp_path=tmp_path, monkeypatch=monkeypatch)
    buy_calls = [c for c in mock_execute.call_args_list if c.args[1] == "BUY"]
    assert buy_calls == [], "no BUY should execute while entries are gated (partial-bar cycle)"


def test_buy_executed_when_entries_allowed(tmp_path, monkeypatch):
    mock_execute = _run_pipeline_with(entries_allowed=True, tmp_path=tmp_path, monkeypatch=monkeypatch)
    buy_calls = [c for c in mock_execute.call_args_list if c.args[1] == "BUY"]
    assert len(buy_calls) == 1
    assert buy_calls[0].args[0] == "AAA"


def test_exits_still_evaluated_when_entries_are_gated(tmp_path, monkeypatch):
    """Stop-loss monitoring must run every cycle regardless of the entries
    gate -- an open position's risk doesn't pause just because it's
    mid-session."""
    monkeypatch.chdir(tmp_path)

    stock_data = {"HELD": _mock_stock_df()}
    # A position whose stop has been breached (Low 99 <= stop 99.5).
    held_positions = pd.DataFrame([
        {"ticker": "HELD", "shares": 10, "avg_price": 110.0, "stop_loss": 99.5},
    ])

    with patch("azalyst._entries_allowed", return_value=False), \
         patch("azalyst.init_db"), \
         patch("azalyst.get_universe", return_value=["HELD"]), \
         patch("azalyst.load_stock_data", return_value=(stock_data, None)), \
         patch("azalyst.fetch_historical", return_value=_mock_stock_df()), \
         patch("azalyst.compute_moving_averages", side_effect=lambda df, *a, **k: df), \
         patch("azalyst.compute_volume_ma", side_effect=lambda df, *a, **k: df), \
         patch("azalyst.detect_bull_regime", return_value={"is_bull": True}), \
         patch("azalyst.compute_rs_ratings", return_value={}), \
         patch("azalyst.apply_stage2_screen", return_value=[]), \
         patch("azalyst.generate_entry_signals", return_value=[]), \
         patch("azalyst.apply_fundamental_filter", side_effect=lambda sigs, **k: sigs), \
         patch("azalyst.get_cash", return_value=100_000.0), \
         patch("azalyst.get_positions", return_value=held_positions), \
         patch("azalyst.execute_trade", return_value=(True, "Success")) as mock_execute:
        azalyst.run_pipeline()

    sell_calls = [c for c in mock_execute.call_args_list if c.args[1] == "SELL"]
    assert len(sell_calls) == 1
    assert sell_calls[0].args[0] == "HELD"
