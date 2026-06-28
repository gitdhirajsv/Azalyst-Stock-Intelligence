import pandas as pd
from config import BENCHMARK_TICKER, MA_PERIODS
from utils import (
    fetch_historical,
    compute_moving_averages,
    compute_volume_ma,
    compute_rs_line
)

def load_stock_data(tickers):
    """
    Download data for all tickers and benchmark.
    Returns:
        stock_data : dict {ticker: DataFrame with indicators}
        benchmark_df : DataFrame for SPY (or chosen benchmark)
    """
    print(f"Loading benchmark {BENCHMARK_TICKER}...")
    benchmark_df = fetch_historical(BENCHMARK_TICKER)
    benchmark_df = compute_moving_averages(benchmark_df)
    benchmark_df = compute_volume_ma(benchmark_df)

    stock_data = {}
    for t in tickers:
        print(f"Loading {t}...")
        try:
            df = fetch_historical(t)
            if df.empty or len(df) < 200:
                continue
            df = compute_moving_averages(df, MA_PERIODS)
            df = compute_volume_ma(df)
            # Relative strength against benchmark
            df['RS'] = compute_rs_line(df, benchmark_df)
            stock_data[t] = df
        except Exception as e:
            print(f"Error loading {t}: {e}")
    return stock_data, benchmark_df
