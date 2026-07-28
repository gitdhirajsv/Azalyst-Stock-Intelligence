import pandas as pd
from config import RISK_PER_TRADE_PCT, MAX_POSITION_PCT, MAX_PORTFOLIO_RISK_PCT

class RiskManager:
    def __init__(self, equity):
        self.equity = equity

    def update_equity(self, new_equity):
        self.equity = new_equity

    def position_size(self, entry_price, stop_loss_price, risk_pct=None):
        """Calculate number of shares based on risk per trade."""
        if risk_pct is None:
            risk_pct = RISK_PER_TRADE_PCT
        risk_amount = self.equity * risk_pct
        risk_per_share = abs(entry_price - stop_loss_price)
        if risk_per_share == 0:
            return 0
        shares = int(risk_amount / risk_per_share)
        # Cap by max position size
        max_shares = int((self.equity * MAX_POSITION_PCT) / entry_price)
        return min(shares, max_shares)

    def check_entry(self, entry_price, stop_loss_price):
        """Check if a new entry is allowed and size it based on risk per trade."""
        shares = self.position_size(entry_price, stop_loss_price)
        if shares <= 0:
            return False, 0, "Position size too small or invalid stop"
        return True, shares, "OK"

    def total_risk_ok(self, open_positions, stock_data):
        """Check if total open risk is under max."""
        total_risk = 0
        for pos in open_positions:
            ticker = pos['ticker']
            df = stock_data.get(ticker)
            if df is None:
                continue
            current_price = df.iloc[-1]['Close']
            stop = pos.get('stop_loss', current_price * 0.95)
            risk_per_share = abs(current_price - stop)
            total_risk += risk_per_share * pos['shares']
        return total_risk < (self.equity * MAX_PORTFOLIO_RISK_PCT)


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
