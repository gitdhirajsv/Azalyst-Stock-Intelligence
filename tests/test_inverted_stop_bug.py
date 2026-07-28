"""STK-05 regression: the AMN-class inverted-stop bug -- a J Law pullback
stop computed as pivot*0.97 could sit ABOVE the actual entry price.

Forensic audit (2026-07-28): the live signals.json contained AMN, a long
BUY_PULLBACK with entry 33.45 and stop_loss 34.66 -- 3.6% ABOVE entry.
detect_meta_pullback only requires 2 of 4 "edges" (BOL-zone proximity to
the pivot is just one), so a pullback can qualify via MA support + volume
contraction alone while sitting well below the pivot -- detect_vcp allows
contractions up to 10% below pivot. Reconstructed: AMN's numbers imply a
pivot of ~35.73, i.e. entry was ~6.4% below pivot -- squarely inside that
allowed band. RiskManager.position_size's abs(entry - stop) (see STK-03)
masked the inversion and sized the trade anyway.
"""

from __future__ import annotations

from unittest.mock import patch

import signal_generator
from signal_generator import _jlaw_stop, _jlaw_signal, generate_entry_signals


# ── _jlaw_stop in isolation ──────────────────────────────────────────────────

def test_normal_case_close_near_pivot_uses_the_pivot_based_stop():
    pivot = 100.0
    close = 99.0  # 1% below pivot -- inside the BOL zone
    stop = _jlaw_stop(pivot, close)
    assert stop == pivot * 0.97
    assert stop < close


def test_amn_reconstruction_close_well_below_pivot_falls_back_to_entry_based_stop():
    pivot = 35.7319587628866
    close = 33.45  # ~6.4% below pivot -- the exact AMN-class scenario
    stop = _jlaw_stop(pivot, close)

    assert stop < close, (
        "the stop must always be below the actual entry price, not just "
        "below the pivot -- the old fixed pivot*0.97 formula produced "
        "34.66 here, ABOVE the 33.45 entry"
    )
    assert stop == close * 0.98


def test_stop_is_below_entry_across_a_range_of_pullback_depths():
    pivot = 100.0
    for pct_below_pivot in (0.005, 0.01, 0.02, 0.04, 0.06, 0.08, 0.10):
        close = pivot * (1 - pct_below_pivot)
        stop = _jlaw_stop(pivot, close)
        assert stop < close, f"stop {stop} must be < close {close} at {pct_below_pivot:.1%} below pivot"


# ── _jlaw_signal integration: reconstructs the exact live bug ───────────────

def _fake_df():
    import pandas as pd
    return pd.DataFrame({"Close": [33.45] * 10})


def test_jlaw_signal_never_emits_a_stop_at_or_above_price():
    vcp = {
        "pivot": 35.7319587628866,
        "base_low": 32.0,
        "contraction_pct": 6.39,
        "vol_contracting": True,
        "breakout_now": False,   # not a fresh breakout -- falls to the pullback branch
        "close": 33.45,
        "volume": 1_000_000.0,
    }
    with patch("signal_generator.detect_vcp", return_value=vcp), \
         patch("signal_generator.detect_meta_pullback", return_value={"meta_score": 3, "is_meta": True}):
        sig = _jlaw_signal("AMN", _fake_df())

    assert sig is not None
    assert sig["stop_loss"] < sig["price"], (
        f"reproduced the live bug: stop_loss={sig['stop_loss']} must be < "
        f"price={sig['price']}"
    )


def test_generate_entry_signals_filters_out_any_residual_inverted_stop():
    """Defense-in-depth check: even if a signal somehow carries an inverted
    stop by the time it reaches the final signal list (e.g. a future
    regression in either path, or in the confluence-merge logic),
    generate_entry_signals must reject it rather than pass it downstream."""
    bad_signal = {
        "ticker": "AMN", "type": "BUY_PULLBACK", "price": 33.45,
        "stop_loss": 34.66, "reason": "synthetic bad signal", "source": "JLAW",
        "sources": ["JLAW"],
    }

    with patch("signal_generator._jlaw_signal", return_value=bad_signal), \
         patch("signal_generator.minervini_signal", return_value=None):
        signals = generate_entry_signals(["AMN"], {"AMN": _fake_df()}, minervini_universe=[])

    assert signals == [], (
        "a signal with stop_loss >= price must never survive "
        "generate_entry_signals, regardless of which path produced it"
    )


def test_generate_entry_signals_keeps_a_valid_signal():
    good_signal = {
        "ticker": "GOOD", "type": "BUY_PULLBACK", "price": 33.45,
        "stop_loss": 32.78, "reason": "synthetic good signal", "source": "JLAW",
        "sources": ["JLAW"],
    }
    with patch("signal_generator._jlaw_signal", return_value=good_signal), \
         patch("signal_generator.minervini_signal", return_value=None):
        signals = generate_entry_signals(["GOOD"], {"GOOD": _fake_df()}, minervini_universe=[])

    assert len(signals) == 1
    assert signals[0]["ticker"] == "GOOD"
