# Azalyst Stock Intelligence - Configuration

# Path to global ticker CSV (you can edit this file to add/remove tickers)
GLOBAL_TICKER_CSV = "global_tickers.csv"

# Risk management (paper trading)
RISK_PER_TRADE_PCT = 0.01       # 1% of equity per trade
MAX_PORTFOLIO_RISK_PCT = 0.06   # 6% total open risk
MAX_POSITION_PCT = 0.25         # max 25% in one stock

# Stage 2 screening thresholds
MIN_PRICE = 5.0                 # minimum stock price (in primary currency)
MIN_AVG_VOLUME = 250000         # shares/day
WITHIN_52W_HIGH_PCT = 0.15      # price within 15% of 52w high
MIN_REVENUE_GROWTH = 0.05       # (optional, not used yet)

# Moving average periods
MA_PERIODS = [10, 20, 50, 150, 200]

# Benchmark for relative strength and regime (US market)
BENCHMARK_TICKER = "SPY"

# Database path
DB_PATH = "database/paper_trades.db"
