import numpy as np
import pandas as pd
import yfinance as yf
from scipy.signal import argrelextrema
from scipy.stats import linregress

def fetch_historical(ticker, period="2y"):
    """Download OHLCV from yfinance."""
    df = yf.download(ticker, period=period, interval="1d", progress=False)
    # Flatten multi-level columns if any
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] for col in df.columns]
    return df

def compute_moving_averages(df, periods=[10,20,50,150,200]):
    for p in periods:
        df[f'MA_{p}'] = df['Close'].rolling(p).mean()
    return df

def compute_volume_ma(df, period=50):
    df[f'Volume_MA_{period}'] = df['Volume'].rolling(period).mean()
    return df

def compute_rs_line(stock_df, benchmark_df):
    """Relative strength: (stock price / benchmark price) normalized."""
    # Align dates
    common = stock_df.index.intersection(benchmark_df.index)
    s = stock_df.loc[common, 'Close']
    b = benchmark_df.loc[common, 'Close']
    rs = (s / b)
    rs = rs / rs.iloc[0] * 100
    return rs

def find_swing_highs_lows(series, order=5):
    """Return indices of local maxima and minima."""
    highs_idx = argrelextrema(series.values, np.greater, order=order)[0]
    lows_idx = argrelextrema(series.values, np.less, order=order)[0]
    return highs_idx, lows_idx

def draw_trendline(x, y):
    """Fit linear trendline; returns slope, intercept."""
    slope, intercept, _, _, _ = linregress(x, y)
    return slope, intercept
