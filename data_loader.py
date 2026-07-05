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
    Download data for all tickers and benchmark in bulk.
    Returns:
        stock_data : dict {ticker: DataFrame with indicators}
        benchmark_df : DataFrame for SPY (or chosen benchmark)
    """
    print(f"Loading benchmark {BENCHMARK_TICKER}...")
    benchmark_df = fetch_historical(BENCHMARK_TICKER)
    benchmark_df = compute_moving_averages(benchmark_df)
    benchmark_df = compute_volume_ma(benchmark_df)

    stock_data = {}
    chunk_size = 500
    
    # Ensure tickers has at least 2 elements to guarantee MultiIndex from yfinance
    if len(tickers) == 1:
        tickers.append(BENCHMARK_TICKER)
        
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i:i+chunk_size]
        if len(chunk) == 1:
            chunk.append(BENCHMARK_TICKER) # Force MultiIndex
            
        print(f"Downloading chunk {i//chunk_size + 1}/{(len(tickers)+chunk_size-1)//chunk_size} ({len(chunk)} tickers)...")
        import yfinance as yf
        try:
            data = yf.download(chunk, period="2y", interval="1d", group_by="ticker", progress=False, threads=True)
        except Exception as e:
            print(f"Error downloading chunk: {e}")
            continue
            
        for t in chunk:
            if t == BENCHMARK_TICKER and t not in tickers[i:i+chunk_size]:
                continue # Skip dummy benchmark if we added it
                
            try:
                if t not in data:
                    continue
                df = data[t].copy()
                df.dropna(how='all', inplace=True)
                
                if df.empty or len(df) < 200:
                    continue
                
                # Liquidity & Penny Stock Filter
                last_price = df['Close'].iloc[-1]
                avg_vol = df['Volume'].tail(20).mean()
                if last_price < 5.0 or avg_vol < 500000:
                    continue
                    
                df = compute_moving_averages(df, MA_PERIODS)
                df = compute_volume_ma(df)
                df['RS'] = compute_rs_line(df, benchmark_df)
                stock_data[t] = df
            except Exception as e:
                pass
                
    return stock_data, benchmark_df
