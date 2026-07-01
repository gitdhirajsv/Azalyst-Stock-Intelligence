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
