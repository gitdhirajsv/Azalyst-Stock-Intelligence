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

def fetch_sector(ticker):
    """
    Best-effort sector/industry lookup for a single ticker (yfinance .info).
    Only meant to be called for the handful of tickers being bought this run,
    not the whole universe — .info is a slow, per-ticker network call.
    """
    try:
        info = yf.Ticker(ticker).info
        sector = info.get('sector')
        industry = info.get('industry')
        if sector and industry:
            return f"{sector} / {industry}"
        return sector or industry or "Unclassified"
    except Exception:
        return "Unclassified"

def compute_moving_averages(df, periods=[10,20,50,150,200]):
    for p in periods:
        df[f'MA_{p}'] = df['Close'].rolling(p).mean()
    return df

def compute_volume_ma(df, period=50):
    df[f'Volume_MA_{period}'] = df['Volume'].rolling(period).mean()
    return df


def compute_rsi(df, period=14):
    """Relative Strength Index (RSI) — per Murphy, Technical Analysis of the
    Financial Markets. RSI measures the speed and magnitude of directional
    price movements. Values > 70 indicate overbought, < 30 oversold."""
    delta = df['Close'].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    # Use Wilder's smoothing (EMA) after the initial SMA seed
    for i in range(period, len(avg_gain)):
        avg_gain.iloc[i] = (avg_gain.iloc[i - 1] * (period - 1) + gain.iloc[i]) / period
        avg_loss.iloc[i] = (avg_loss.iloc[i - 1] * (period - 1) + loss.iloc[i]) / period
    rs = avg_gain / avg_loss
    df['RSI'] = 100 - (100 / (1 + rs))
    return df


def compute_macd(df, fast=12, slow=26, signal=9):
    """MACD (Moving Average Convergence Divergence) — per Murphy.
    MACD line = EMA(fast) - EMA(slow); Signal line = EMA(MACD, signal).
    Histogram = MACD - Signal. Bullish when MACD > Signal."""
    ema_fast = df['Close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['Close'].ewm(span=slow, adjust=False).mean()
    df['MACD'] = ema_fast - ema_slow
    df['MACD_Signal'] = df['MACD'].ewm(span=signal, adjust=False).mean()
    df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']
    return df


def compute_atr(df, period=14):
    """Average True Range — used for volatility-normalized stop placement."""
    high = df['High']
    low = df['Low']
    close = df['Close'].shift(1)
    tr = pd.concat([
        high - low,
        (high - close).abs(),
        (low - close).abs(),
    ], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(window=period).mean()
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
