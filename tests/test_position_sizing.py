"""STK-03 regression: position sizing must bound the worst-case LOSS (an
adverse gap), not just the theoretical risk formula, and must reject an
invalid (at/above entry) stop instead of masking it with abs().

Forensic audit (2026-07-28): shares = (equity*1%) / |entry - stop|, capped
only by MAX_POSITION_PCT (25%). Since uncapped notional = equity *
(risk_pct / stop_pct), any stop tighter than risk_pct/MAX_POSITION_PCT
(4% at the defaults) produces >=25% notional and clips at the cap. The
J-Law pullback entry structurally yields exactly such a stop (close within
~2% of pivot, stop at pivot*0.97) -- so "1% risk sizing" was, in practice,
always a fixed ~25%-of-equity bet, position after position.
"""

from __future__ import annotations

import pytest

from risk_manager import RiskManager, GAP_RISK_PCT, GAP_RISK_BUDGET_PCT
from config import MAX_POSITION_PCT, RISK_PER_TRADE_PCT


def test_tight_stop_no_longer_balloons_to_the_25pct_position_cap():
    """The exact structural case from the live book: entry near the pivot,
    stop at pivot*0.97 (~3% away) -- previously this alone produced a
    ~25%-of-equity position regardless of how tight the stop was."""
    equity = 100_000.0
    rm = RiskManager(equity)
    entry = 45.008
    stop = entry * 0.97  # ~3% away, matches the live PACS-class trade

    shares = rm.position_size(entry, stop)
    notional = shares * entry

    old_uncapped_notional = equity * MAX_POSITION_PCT  # what the old code produced
    assert notional < old_uncapped_notional * 0.85, (
        f"a 3%-away stop must no longer size to the old 25% cap; got "
        f"notional={notional:.2f} vs old cap {old_uncapped_notional:.2f}"
    )


def test_worst_case_gap_loss_is_bounded_regardless_of_stop_tightness():
    """For a range of stop distances (including very tight ones), an 8%
    adverse gap must never cost more than GAP_RISK_BUDGET_PCT of equity."""
    equity = 100_000.0
    rm = RiskManager(equity)
    entry = 50.0

    for stop_pct in (0.01, 0.02, 0.03, 0.05, 0.08, 0.15):
        stop = entry * (1 - stop_pct)
        shares = rm.position_size(entry, stop)
        notional = shares * entry
        worst_case_gap_loss = notional * GAP_RISK_PCT
        assert worst_case_gap_loss <= equity * GAP_RISK_BUDGET_PCT * 1.01, (
            f"stop_pct={stop_pct}: an {GAP_RISK_PCT:.0%} gap would cost "
            f"{worst_case_gap_loss:.2f}, exceeding the "
            f"{GAP_RISK_BUDGET_PCT:.1%}-of-equity budget "
            f"({equity * GAP_RISK_BUDGET_PCT:.2f})"
        )


def test_normal_wide_stop_still_sized_by_the_1pct_risk_formula():
    """A stop wide enough that 1%-risk sizing doesn't hit either cap should
    behave exactly as the original formula intended."""
    equity = 100_000.0
    rm = RiskManager(equity)
    entry = 100.0
    stop = 90.0  # 10% away -- risk_amount / risk_per_share = 1000/10 = 100 shares

    shares = rm.position_size(entry, stop)
    assert shares == 100


def test_stop_at_entry_is_rejected_not_sized_via_abs():
    rm = RiskManager(100_000.0)
    shares = rm.position_size(entry_price=50.0, stop_loss_price=50.0)
    assert shares == 0


def test_stop_above_entry_is_rejected_not_masked_by_abs():
    """The exact AMN-class bug: a long signal with stop_loss ABOVE entry.
    abs(entry - stop) would previously produce a small positive risk-per-
    share and size the trade anyway."""
    rm = RiskManager(100_000.0)
    shares = rm.position_size(entry_price=33.45, stop_loss_price=34.66)
    assert shares == 0, (
        "a stop above entry for a long must be rejected outright, not "
        "sized via abs(entry - stop) as if it were a valid tight stop"
    )


def test_none_stop_is_rejected():
    rm = RiskManager(100_000.0)
    assert rm.position_size(entry_price=50.0, stop_loss_price=None) == 0


def test_check_entry_reports_rejection_for_invalid_stop():
    rm = RiskManager(100_000.0)
    allowed, shares, reason = rm.check_entry(33.45, 34.66)
    assert allowed is False
    assert shares == 0
