import pandas as pd
from config import (
    RISK_PER_TRADE_PCT, MAX_POSITION_PCT, MAX_PORTFOLIO_RISK_PCT,
    MAX_OPEN_POSITIONS, MAX_POSITIONS_PER_SECTOR,
)

# STK-03 remediation (forensic audit 2026-07-28): the tight structural stops
# this strategy actually uses (J Law pullback stops are pivot*0.97, often
# 2-4% from entry) meant risk_amount / risk_per_share ballooned toward
# MAX_POSITION_PCT on every trade -- any stop tighter than
# RISK_PER_TRADE_PCT / MAX_POSITION_PCT (4%, at the defaults) hits the 25%
# cap. Sizing was nominally "1% risk" but every real trade was actually a
# 25%-of-equity bet, because the stop that sizes the trade is not
# necessarily the stop it exits at -- a gap can jump straight past it.
# GAP_RISK_PCT is an assumed worst-case single-session adverse gap (matches
# the Minervini Trend-Template's own 8%-max-structural-risk rule); no
# position may be sized so large that an 8% gap costs more than
# GAP_RISK_BUDGET_PCT of equity, regardless of how tight the nominal stop is.
GAP_RISK_PCT = 0.08
GAP_RISK_BUDGET_PCT = 0.015


class RiskManager:
    def __init__(self, equity):
        self.equity = equity

    def update_equity(self, new_equity):
        self.equity = new_equity

    def position_size(self, entry_price, stop_loss_price, risk_pct=None):
        """Calculate number of shares based on risk per trade, bounded by
        worst-case gap risk and the max-position cap -- whichever of the
        three is tightest wins.

        Returns 0 if stop_loss_price is missing or at/above entry_price: a
        stop at or above cost is nonsensical for a long (the STK-05
        inverted-stop bug class) and must never be sized off, not silently
        masked by taking abs() of the distance.
        """
        if risk_pct is None:
            risk_pct = RISK_PER_TRADE_PCT
        if stop_loss_price is None or stop_loss_price <= 0 or stop_loss_price >= entry_price:
            return 0

        risk_amount = self.equity * risk_pct
        risk_per_share = entry_price - stop_loss_price
        shares_by_risk = int(risk_amount / risk_per_share)

        max_shares_by_position_cap = int((self.equity * MAX_POSITION_PCT) / entry_price)
        max_shares_by_gap_risk = int(
            (self.equity * GAP_RISK_BUDGET_PCT) / (entry_price * GAP_RISK_PCT)
        )

        return max(0, min(shares_by_risk, max_shares_by_position_cap, max_shares_by_gap_risk))

    def check_entry(self, entry_price, stop_loss_price):
        """Check if a new entry is allowed and size it based on risk per trade."""
        shares = self.position_size(entry_price, stop_loss_price)
        if shares <= 0:
            return False, 0, "Position size too small or invalid stop"
        return True, shares, "OK"

    def total_risk_ok(self, open_positions, stock_data):
        """True if total open risk (sum of per-position dollar risk to stop)
        is under MAX_PORTFOLIO_RISK_PCT of equity.

        STK-04 remediation (forensic audit 2026-07-28): this had zero call
        sites in the live pipeline -- nothing ever checked aggregate
        portfolio risk, so nothing prevented stacking many maximally-sized
        positions. `open_positions` is an iterable of dict-likes (e.g.
        paper_trader.get_positions().to_dict('records'), plus any positions
        opened earlier in the same scan that aren't in the DB yet -- see
        azalyst.py's entry loop). Also fixed: `pos.get('stop_loss', ...)`
        only applies its default when the KEY is missing, not when the
        value is None -- since STK-01 the column always exists (value None
        for legacy/unset rows), so the old default silently never fired;
        this now checks `is None` explicitly. A stop that's missing or
        invalid (>= current price, the STK-05 inverted-stop bug class)
        falls back to a 5%-of-price placeholder, same as the original
        intent.
        """
        total_risk = 0.0
        for pos in open_positions:
            ticker = pos['ticker']
            df = stock_data.get(ticker)
            if df is None:
                continue
            current_price = df.iloc[-1]['Close']
            stop = pos.get('stop_loss')
            if stop is None or pd.isna(stop) or stop <= 0 or stop >= current_price:
                stop = current_price * 0.95
            risk_per_share = current_price - stop
            total_risk += risk_per_share * pos['shares']
        return bool(total_risk < (self.equity * MAX_PORTFOLIO_RISK_PCT))


def _top_level_sector(sector):
    """utils.fetch_sector returns "{GICS sector} / {industry}" (e.g.
    "Healthcare / Biotechnology") -- the fine industry differs company to
    company even within the same broad sector, so an exact-string match on
    the combined value would almost never catch real concentration (the
    live book's CNC was "Healthcare / Healthcare Plans" while ROIV/KYMR
    were "Healthcare / Biotechnology" -- three different industries, one
    sector). The diversification cap groups by the GICS sector only."""
    if not sector:
        return "Unclassified"
    return sector.split(" / ")[0].strip()


def check_diversification(combined_positions, sector):
    """True if adding one more position in `sector` stays within
    MAX_OPEN_POSITIONS and MAX_POSITIONS_PER_SECTOR. Returns (ok, reason).

    STK-04 remediation (forensic audit 2026-07-28): no position-count or
    sector-concentration limit of any kind existed -- sector was fetched
    only after entry was already approved, purely as dashboard metadata.
    The live book ended up with 3 of 4 positions in healthcare/biotech by
    accident. `combined_positions` is the existing DB book plus any
    positions already opened earlier in the same scan (see azalyst.py's
    entry loop -- positions is not re-queried mid-loop).
    """
    if len(combined_positions) >= MAX_OPEN_POSITIONS:
        return False, f"max open positions ({MAX_OPEN_POSITIONS}) reached"
    target = _top_level_sector(sector)
    sector_count = sum(
        1 for p in combined_positions if _top_level_sector(p.get('sector')) == target
    )
    if sector_count >= MAX_POSITIONS_PER_SECTOR:
        return False, f"sector '{target}' already at cap ({MAX_POSITIONS_PER_SECTOR})"
    return True, "OK"


# ETF-style STK-02 remediation note (forensic audit 2026-07-28): the only
# live exit used to be a hardcoded "Dummy exit rule" (avg_price * 0.92)
# checked against the day's Close -- completely ignoring the structural
# stop that sized the position (see STK-01, which persists stop_loss with
# the row). RCUS was sized off a stop ~2-4% from entry and still lost
# -10.1%, because that stop was never enforced.
CATASTROPHE_BACKSTOP_PCT = 0.85


def evaluate_stop_loss_exits(positions, stock_data):
    """Return [(ticker, shares, fill_price, reason), ...] for positions whose
    stop was touched today.

    Checks the PERSISTED structural stop against the day's Low (a stop can
    be touched intraday even if Close recovers), with a gap-aware fill: if
    the session's Open itself gapped below the stop, the stop price was
    never actually available, so the fill is the Open, not a price nobody
    could get.

    avg_price * CATASTROPHE_BACKSTOP_PCT (0.85) is kept only as a backstop:
    it fires when the persisted stop is missing (a pre-migration or
    corrupted row) or nonsensical for a long (at/above cost -- the STK-05
    inverted-stop class of bug), never as the primary exit. Whichever level
    is tighter (closer to current price) is used, so a valid, tighter
    structural stop always takes precedence over the wider backstop.
    """
    exits = []
    if positions is None or positions.empty:
        return exits

    for _, pos in positions.iterrows():
        ticker = pos['ticker']
        if ticker not in stock_data:
            continue

        shares = pos['shares']
        avg_price = pos['avg_price']
        stop_loss = pos.get('stop_loss')

        day = stock_data[ticker].iloc[-1]
        day_low = day['Low']
        day_open = day['Open']

        catastrophe_level = avg_price * CATASTROPHE_BACKSTOP_PCT
        has_valid_stop = pd.notna(stop_loss) and 0 < stop_loss < avg_price
        effective_stop = max(stop_loss, catastrophe_level) if has_valid_stop else catastrophe_level

        if day_low <= effective_stop:
            fill_price = day_open if day_open < effective_stop else effective_stop
            reason = "Stop Loss" if has_valid_stop and effective_stop == stop_loss else "Catastrophe backstop"
            exits.append((ticker, shares, fill_price, reason))

    return exits
