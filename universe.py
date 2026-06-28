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
        if 'ticker' in df.columns:
            return df['ticker'].tolist()
        else:
            return df.iloc[:,0].tolist()  # first column
    else:
        # Fallback default global list
        return [
            "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA",
            "0700.HK", "9988.HK", "VOD.L", "BABA", "005930.KS", "7203.T"
        ]
