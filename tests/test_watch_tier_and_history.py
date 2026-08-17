"""S3 regression: a name rejected purely on TIMING becomes a WATCH entry with an
explicit trigger price instead of vanishing, and the signal record survives the
next run.

Before S3, minervini_signal() dropped and discarded any name whose close was
above pivot*1.05 (extended) or whose structural stop implied more than 8% risk,
and any name still coiling under its pivot -- the exact buy-zone window the
strategy trades. azalyst.py then rewrote signals.json with mode 'w' every cycle
(up to 8 cycles a day), so nothing was left to alert on or to measure
screened-but-not-traded hit rates against.

The hard ENTRY gates are unchanged. These tests also pin that: a WATCH entry
carries no executable price and no stop, and never reaches the paper trader.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd
import pytest

import azalyst
import signal_history
from minervini import (
    TIMING_BUY_NOW, TIMING_BUY_ON_BREAKOUT, TIMING_WAIT_FOR_BREAKOUT,
    TIMING_WAIT_FOR_PULLBACK, max_entry_price, minervini_signal, minervini_watch,
)
from signal_generator import generate_watch_signals


def _df():
    """minervini_watch reads the frame only through passes_trend_template and
    detect_vcp, both patched below."""
    return pd.DataFrame({"Close": [100.0] * 10})


def _entry_df():
    """minervini_signal additionally reads the volume columns directly."""
    return pd.DataFrame({
        "Close": [101.0] * 3,
        "Volume": [2_000_000.0] * 3,
        "Volume_MA_50": [1_000_000.0] * 3,
    })


def _vcp(close, pivot=100.0, base_low=94.0, breakout_now=False, contraction_pct=6.0):
    return {
        "pivot": pivot,
        "base_low": base_low,
        "contraction_pct": contraction_pct,
        "vol_contracting": True,
        "breakout_now": breakout_now,
        "close": close,
        "volume": 2_000_000.0,
    }


def _watch(close, df=None, **kw):
    with patch("minervini.passes_trend_template", return_value=(True, {"c8_rs_rating_ok": True})), \
         patch("minervini.detect_vcp", return_value=_vcp(close, **kw)):
        return minervini_watch("AAA", df if df is not None else _df(), rs_rating=93)


# ── the trigger price is derived from the real gates ────────────────────────

def test_max_entry_price_is_the_tighter_of_the_buy_zone_and_the_8pct_stop():
    # base_low 94 -> 94/0.92 = 102.17 binds before the 105.00 buy-zone top
    assert max_entry_price(100.0, 94.0) == pytest.approx(102.1739, abs=1e-4)
    # base_low 98 -> 98/0.92 = 106.52, so the 5% buy zone binds instead
    assert max_entry_price(100.0, 98.0) == pytest.approx(105.0)
    # no base low available -> buy zone only
    assert max_entry_price(100.0, None) == pytest.approx(105.0)


# ── the four timing states ─────────────────────────────────────────────────

def test_extended_name_becomes_a_wait_for_pullback_watch_not_a_dropped_name():
    w = _watch(close=110.0, breakout_now=True)
    assert w is not None, "an extended breakout must be recorded, not discarded"
    assert w["timing"] == TIMING_WAIT_FOR_PULLBACK
    assert w["trigger_price"] == pytest.approx(102.1739, abs=1e-4)
    assert w["trigger_price"] < w["buy_zone_high"]


def test_price_through_the_pivot_without_volume_is_buy_on_breakout():
    w = _watch(close=101.0, breakout_now=False)
    assert w["timing"] == TIMING_BUY_ON_BREAKOUT
    assert w["trigger_price"] == 100.0


def test_coiling_just_under_the_pivot_is_buy_on_breakout():
    w = _watch(close=99.0)
    assert w["timing"] == TIMING_BUY_ON_BREAKOUT
    assert w["trigger_price"] == 100.0


def test_well_below_the_pivot_is_wait_for_breakout():
    w = _watch(close=90.0)
    assert w["timing"] == TIMING_WAIT_FOR_BREAKOUT
    assert w["trigger_price"] == 100.0


def test_a_confirmed_in_zone_breakout_is_an_entry_not_a_watch():
    assert _watch(close=101.0, breakout_now=True) is None


def test_a_loose_base_is_not_a_watch_candidate_at_any_price():
    assert _watch(close=99.0, contraction_pct=15.0) is None


# ── the hard entry gates are untouched ─────────────────────────────────────

def test_watch_entry_is_structurally_unexecutable():
    w = _watch(close=110.0)
    assert "price" not in w, "a watch entry must carry no executable price"
    assert "stop_loss" not in w, "a watch entry must carry no stop -- it cannot be sized"
    assert w["actionable_now"] is False
    assert w["stop_reference"] is not None  # reference only, deliberately named apart


def test_entry_signal_still_fires_and_now_carries_buy_now_plus_a_trigger():
    with patch("minervini.passes_trend_template", return_value=(True, {})), \
         patch("minervini.detect_vcp", return_value=_vcp(101.0, breakout_now=True)):
        sig = minervini_signal("AAA", _entry_df(), rs_rating=93)

    assert sig is not None, "the unchanged entry gates must still pass this setup"
    assert sig["timing"] == TIMING_BUY_NOW
    assert sig["trigger_price"] == pytest.approx(101.0)
    assert sig["price"] == pytest.approx(101.0)
    assert sig["stop_loss"] < sig["price"]


def test_extended_name_is_still_rejected_by_the_entry_path():
    """The WATCH tier must not create a back door: the same name that produces a
    WAIT FOR PULLBACK watch must still produce NO entry signal."""
    extended_df = pd.DataFrame({
        "Close": [110.0] * 3, "Volume": [2_000_000.0] * 3, "Volume_MA_50": [1_000_000.0] * 3,
    })
    with patch("minervini.passes_trend_template", return_value=(True, {})), \
         patch("minervini.detect_vcp", return_value=_vcp(110.0, breakout_now=True)):
        assert minervini_signal("AAA", extended_df, rs_rating=93) is None


# ── generate_watch_signals ─────────────────────────────────────────────────

def _fake_watch(ticker, df, rs_rating=None, timing=TIMING_WAIT_FOR_PULLBACK):
    return {"ticker": ticker, "kind": "WATCH", "timing": timing,
            "trigger_price": 102.17, "rs_rating": rs_rating, "actionable_now": False}


def test_generate_watch_signals_excludes_tickers_that_already_produced_an_entry():
    with patch("signal_generator.minervini_watch", side_effect=_fake_watch):
        watch = generate_watch_signals(
            ["AAA", "BBB"], {"AAA": _df(), "BBB": _df()},
            rs_ratings={"AAA": 95, "BBB": 90}, exclude_tickers={"AAA"},
        )
    assert [w["ticker"] for w in watch] == ["BBB"], (
        "a name is either actionable or on watch, never both"
    )


def test_generate_watch_signals_ranks_closest_to_tradeable_first_and_caps():
    def _side_effect(ticker, df, rs_rating=None):
        timing = TIMING_BUY_ON_BREAKOUT if ticker == "BBB" else TIMING_WAIT_FOR_BREAKOUT
        return _fake_watch(ticker, df, rs_rating=rs_rating, timing=timing)

    with patch("signal_generator.minervini_watch", side_effect=_side_effect):
        watch = generate_watch_signals(
            ["AAA", "BBB", "CCC"],
            {"AAA": _df(), "BBB": _df(), "CCC": _df()},
            rs_ratings={"AAA": 99, "BBB": 90, "CCC": 95}, limit=2,
        )
    assert [w["ticker"] for w in watch] == ["BBB", "AAA"]


# ── pipeline integration: a watch entry never becomes a position ───────────

def _mock_stock_df():
    idx = pd.bdate_range(end="2026-08-18", periods=5)
    return pd.DataFrame({
        "Open": [100.0] * 5, "High": [101.0] * 5, "Low": [99.0] * 5, "Close": [100.0] * 5,
        "Volume": [1_000_000] * 5,
    }, index=idx)


def test_watch_entries_never_reach_the_paper_trader(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)   # signals/watchlist/history writes stay out of the repo

    stock_data = {"AAA": _mock_stock_df()}
    watch_entry = {
        "ticker": "AAA", "kind": "WATCH", "timing": TIMING_WAIT_FOR_PULLBACK,
        "trigger_price": 102.17, "rs_rating": 95, "actionable_now": False,
        "reason": "synthetic watch", "source": "MINERVINI",
    }

    with patch("azalyst._entries_allowed", return_value=True), \
         patch("azalyst.init_db"), \
         patch("azalyst.get_universe", return_value=["AAA"]), \
         patch("azalyst.load_stock_data", return_value=(stock_data, None)), \
         patch("azalyst.fetch_historical", return_value=_mock_stock_df()), \
         patch("azalyst.compute_moving_averages", side_effect=lambda df, *a, **k: df), \
         patch("azalyst.compute_volume_ma", side_effect=lambda df, *a, **k: df), \
         patch("azalyst.detect_bull_regime", return_value={"is_bull": True}), \
         patch("azalyst.compute_rs_ratings", return_value={"AAA": 95}), \
         patch("azalyst.apply_stage2_screen", return_value=["AAA"]), \
         patch("azalyst.generate_entry_signals", return_value=[]), \
         patch("azalyst.generate_watch_signals", return_value=[watch_entry]), \
         patch("azalyst.apply_fundamental_filter", side_effect=lambda sigs, **k: sigs), \
         patch("azalyst.get_cash", return_value=100_000.0), \
         patch("azalyst.get_positions", return_value=pd.DataFrame()), \
         patch("azalyst.fetch_sector", return_value="Technology"), \
         patch("azalyst.execute_trade", return_value=(True, "Success")) as mock_execute:
        azalyst.run_pipeline()

    buys = [c for c in mock_execute.call_args_list if c.args[1] == "BUY"]
    assert buys == [], "a WATCH entry must never be executed as a position"

    # The dashboard's consumer contract: signals.json stays a JSON ARRAY of
    # ACTIONABLE entries only (generate_dashboard.py iterates it directly).
    signals_json = json.loads((tmp_path / "signals.json").read_text())
    assert isinstance(signals_json, list)
    assert signals_json == []

    watchlist = json.loads((tmp_path / "watchlist.json").read_text())
    assert [w["ticker"] for w in watchlist] == ["AAA"]

    history = signal_history.load_history(str(tmp_path / "history" / "signals.jsonl"))
    assert len(history) == 1
    assert history[0]["kind"] == "WATCH"
    assert history[0]["trigger_price"] == 102.17


# ── append-only history ────────────────────────────────────────────────────

_RUN_1 = datetime(2026, 8, 18, 22, 30, tzinfo=timezone.utc)
_RUN_2 = datetime(2026, 8, 18, 23, 30, tzinfo=timezone.utc)
_RUN_NEXT_DAY = datetime(2026, 8, 19, 22, 30, tzinfo=timezone.utc)


def _entry():
    return {"ticker": "AAA", "timing": TIMING_BUY_NOW, "trigger_price": 101.0,
            "price": 101.0, "stop_loss": 94.0, "rs_rating": 95,
            "source": "MINERVINI", "reason": "entry"}


def _watch_row(timing=TIMING_WAIT_FOR_PULLBACK):
    return {"ticker": "BBB", "timing": timing, "trigger_price": 102.17,
            "stop_reference": 94.0, "projected_risk_pct": 8.0, "rs_rating": 91,
            "source": "MINERVINI", "reason": "watch"}


def test_history_appends_both_kinds_and_dedupes_within_the_day(tmp_path):
    path = str(tmp_path / "history" / "signals.jsonl")

    assert signal_history.append_observations([_entry()], [_watch_row()], run_at=_RUN_1, path=path) == 2
    # the same scan an hour later must not pad the file
    assert signal_history.append_observations([_entry()], [_watch_row()], run_at=_RUN_2, path=path) == 0

    rows = signal_history.load_history(path)
    assert len(rows) == 2
    assert {r["kind"] for r in rows} == {"ENTRY", "WATCH"}
    assert rows[0]["timing"] == TIMING_BUY_NOW
    assert rows[1]["trigger_price"] == 102.17


def test_history_records_a_timing_state_change_on_the_same_day(tmp_path):
    path = str(tmp_path / "history" / "signals.jsonl")
    assert signal_history.append_observations(
        [], [_watch_row(TIMING_WAIT_FOR_BREAKOUT)], run_at=_RUN_1, path=path) == 1
    assert signal_history.append_observations(
        [], [_watch_row(TIMING_BUY_ON_BREAKOUT)], run_at=_RUN_2, path=path) == 1
    assert [r["timing"] for r in signal_history.load_history(path)] == [
        TIMING_WAIT_FOR_BREAKOUT, TIMING_BUY_ON_BREAKOUT
    ]


def test_a_later_empty_run_cannot_erase_the_record(tmp_path):
    """The regression this exists for: signals.json is rewritten with mode 'w'
    every cycle, so an empty scan wiped out everything the engine had seen."""
    path = str(tmp_path / "history" / "signals.jsonl")
    signal_history.append_observations([_entry()], [], run_at=_RUN_1, path=path)
    signal_history.append_observations([], [], run_at=_RUN_NEXT_DAY, path=path)

    rows = signal_history.load_history(path)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAA"
    assert rows[0]["date"] == "2026-08-18"


def test_history_skips_a_malformed_line(tmp_path):
    path = tmp_path / "history" / "signals.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"date": "2026-08-18", "ticker": "AAA", "kind": "ENTRY"}\nnot json\n')
    assert len(signal_history.load_history(str(path))) == 1


def test_load_history_on_a_missing_file_is_empty(tmp_path):
    assert signal_history.load_history(str(tmp_path / "nope.jsonl")) == []
