"""STK-02 regression: the persisted stop (STK-01) must actually be enforced,
with a gap-aware fill, replacing the hardcoded "Dummy exit rule".

Forensic audit (2026-07-28): the only live exit was
`if current_price < avg_price * 0.92` checked against the day's Close --
ignoring the tight structural stop that sized the position entirely. RCUS
was sized off a stop ~2-4% from entry (via RiskManager.position_size) and
still lost -10.1%, because that stop was never enforced; the only exit that
existed was 8% late, and only checked once the *close* had already fallen
through it.
"""

from __future__ import annotations

import pandas as pd
import pytest

from risk_manager import evaluate_stop_loss_exits, CATASTROPHE_BACKSTOP_PCT


def _positions_df(rows):
    return pd.DataFrame(rows)


def _day(open_, high, low, close):
    return pd.DataFrame({
        "Open": [open_], "High": [high], "Low": [low], "Close": [close],
    })


def test_stop_touched_intraday_exits_even_if_close_recovered():
    """This is the case the old Close-only check structurally could not
    catch: price dips through the stop intraday, then closes back above
    avg_price -- a real stop-loss order would still have filled."""
    positions = _positions_df([
        {"ticker": "RCUS", "shares": 100, "avg_price": 30.0, "stop_loss": 27.83},
    ])
    stock_data = {"RCUS": _day(open_=29.8, high=30.5, low=27.5, close=30.2)}

    exits = evaluate_stop_loss_exits(positions, stock_data)

    assert len(exits) == 1
    ticker, shares, fill_price, reason = exits[0]
    assert ticker == "RCUS"
    assert shares == 100
    assert reason == "Stop Loss"
    assert fill_price == pytest.approx(27.83)


def test_no_exit_when_low_stays_above_the_stop():
    positions = _positions_df([
        {"ticker": "RCUS", "shares": 100, "avg_price": 30.0, "stop_loss": 27.83},
    ])
    stock_data = {"RCUS": _day(open_=29.8, high=30.5, low=28.0, close=29.9)}

    assert evaluate_stop_loss_exits(positions, stock_data) == []


def test_gap_down_below_stop_fills_at_open_not_the_stop_price():
    """The stop price was never actually available -- a real broker fills
    at the next tradeable price (the Open), not a price that gapped past."""
    positions = _positions_df([
        {"ticker": "RCUS", "shares": 100, "avg_price": 30.0, "stop_loss": 27.83},
    ])
    stock_data = {"RCUS": _day(open_=25.0, high=25.5, low=24.8, close=25.1)}

    exits = evaluate_stop_loss_exits(positions, stock_data)

    assert len(exits) == 1
    _, _, fill_price, reason = exits[0]
    assert fill_price == pytest.approx(25.0), (
        "a gap below the stop must fill at Open, not grant the stop price "
        "nobody could actually trade at"
    )
    assert reason == "Stop Loss"


def test_missing_stop_falls_back_to_catastrophe_backstop():
    positions = _positions_df([
        {"ticker": "PACS", "shares": 552, "avg_price": 45.008, "stop_loss": None},
    ])
    backstop = 45.008 * CATASTROPHE_BACKSTOP_PCT
    stock_data = {"PACS": _day(open_=backstop + 1, high=backstop + 1.5, low=backstop - 0.5, close=backstop + 0.2)}

    exits = evaluate_stop_loss_exits(positions, stock_data)

    assert len(exits) == 1
    _, _, fill_price, reason = exits[0]
    assert reason == "Catastrophe backstop"
    assert fill_price == pytest.approx(backstop)


def test_invalid_stop_at_or_above_cost_falls_back_to_catastrophe_backstop():
    """Defense-in-depth against the STK-05 inverted-stop bug class: a stop
    at/above entry is nonsensical for a long and must not be trusted as the
    enforcement level."""
    positions = _positions_df([
        {"ticker": "AMN", "shares": 200, "avg_price": 33.45, "stop_loss": 34.66},
    ])
    backstop = 33.45 * CATASTROPHE_BACKSTOP_PCT
    stock_data = {"AMN": _day(open_=backstop + 1, high=backstop + 1.5, low=backstop - 0.5, close=backstop + 0.2)}

    exits = evaluate_stop_loss_exits(positions, stock_data)

    assert len(exits) == 1
    _, _, _, reason = exits[0]
    assert reason == "Catastrophe backstop", (
        "an inverted stop (>= entry) must never be trusted as the "
        "enforcement level -- it would trigger instantly and for the "
        "wrong reason"
    )


def test_valid_tight_stop_takes_precedence_over_wider_backstop():
    """A real, tighter stop must trigger before the wider catastrophe
    backstop would ever be reached."""
    positions = _positions_df([
        {"ticker": "RCUS", "shares": 100, "avg_price": 30.0, "stop_loss": 29.0},  # ~3.3% stop
    ])
    # Low touches the tight stop but is nowhere near the 15% backstop.
    stock_data = {"RCUS": _day(open_=29.8, high=30.2, low=28.9, close=29.5)}

    exits = evaluate_stop_loss_exits(positions, stock_data)

    assert len(exits) == 1
    _, _, fill_price, reason = exits[0]
    assert reason == "Stop Loss"
    assert fill_price == pytest.approx(29.0)


def test_no_positions_returns_empty():
    assert evaluate_stop_loss_exits(pd.DataFrame(), {}) == []


def test_ticker_with_no_market_data_is_skipped_not_errored():
    positions = _positions_df([
        {"ticker": "UNKNOWN", "shares": 10, "avg_price": 50.0, "stop_loss": 46.0},
    ])
    assert evaluate_stop_loss_exits(positions, {}) == []
