import pandas as pd
import os
from config import GLOBAL_TICKER_CSV

def get_universe():
    """
    Load ticker list from CSV. Falls back to a small default list if file missing.
    """
    if os.path.exists(GLOBAL_TICKER_CSV):
        df = pd.read_csv(GLOBAL_TICKER_CSV)
        # Expect column named 'ticker'
        col = df['ticker'] if 'ticker' in df.columns else df.iloc[:, 0]
        # Sanitize: drop NaN/blank rows and coerce to clean strings. A stray NaN
        # (float) reaches yfinance as a non-string and kills a whole 500-ticker
        # download chunk ("expected string or bytes-like object, got 'float'").
        tickers = (
            col.dropna()
               .astype(str)
               .str.strip()
        )
        tickers = tickers[(tickers != "") & (tickers.str.lower() != "nan")]
        # De-duplicate while preserving order.
        return list(dict.fromkeys(tickers.tolist()))
    else:
        # Fallback default global list
        return [
            "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA",
            "0700.HK", "9988.HK", "VOD.L", "BABA", "005930.KS", "7203.T"
        ]
