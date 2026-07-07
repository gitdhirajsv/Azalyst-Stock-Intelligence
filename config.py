# Azalyst Stock Intelligence - Configuration

# Path to global ticker CSV (you can edit this file to add/remove tickers)
GLOBAL_TICKER_CSV = "global_tickers.csv"

# Risk management (paper trading)
RISK_PER_TRADE_PCT = 0.01       # 1% of equity per trade
MAX_PORTFOLIO_RISK_PCT = 0.06   # 6% total open risk
MAX_POSITION_PCT = 0.25         # max 25% in one stock

# Stage 2 screening thresholds
MIN_PRICE = 12.0                # Minervini min price $12 (O'Neil standard is $20). Was 5.0.
MIN_AVG_VOLUME = 250000         # shares/day
WITHIN_52W_HIGH_PCT = 0.15      # price within 15% of 52w high (personalized, tighter than 25%)

# Relative Strength Rating gate (IBD 1-99 percentile). Minervini's watershed is 89.
# Applied to BOTH the J Law watchlist and the Minervini Trend-Template path.
RS_RATING_MIN = 89              # was 70; lecture: ">= 89 is Mark's dividing line"

# Fundamental screen (revenue, not profit) -- applied to actionable BUY signals.
MIN_REVENUE_GROWTH = 0.05       # most-recent-quarter revenue must grow > 5% YoY
REQUIRE_SALES_ACCELERATION = True   # require revenue growth to be accelerating
# When a signal's revenue can't be verified, keep+flag it (False) rather than
# dropping it, so a data outage never silently empties the buy list. Set True to
# hard-require verified fundamentals before any buy.
REQUIRE_VERIFIED_FUNDAMENTALS = False

# Moving average periods
MA_PERIODS = [10, 20, 50, 150, 200]

# Benchmark for relative strength and regime (US market)
BENCHMARK_TICKER = "SPY"

# Database path
DB_PATH = "database/paper_trades.db"
