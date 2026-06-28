import numpy as np
from config import MIN_PRICE, MIN_AVG_VOLUME, WITHIN_52W_HIGH_PCT

def apply_stage2_screen(stock_data):
    """
    Return list of tickers passing Stage 2 + J Law filters.
    """
    passed = []
    for ticker, df in stock_data.items():
        if len(df) < 200:
            continue
        last = df.iloc[-1]
        close = last['Close']
        ma50 = last.get('MA_50')
        ma150 = last.get('MA_150')
        ma200 = last.get('MA_200')

        if any(pd.isna(x) for x in [ma50, ma150, ma200]):
            continue

        # Core conditions
        cond1 = close > ma150
        cond2 = close > ma200
        cond3 = ma150 > ma200
        cond4 = (ma50 > ma150) and (ma50 > ma200)
        cond5 = close > ma50

        # 52-week
        high52 = df['High'].rolling(252).max().iloc[-1]
        low52 = df['Low'].rolling(252).min().iloc[-1]
        cond6 = close >= (1.3 * low52)  # > 30% above low
        cond7 = close >= (1 - WITHIN_52W_HIGH_PCT) * high52

        # Volume
        avg_vol = df['Volume'].rolling(50).mean().iloc[-1]
        cond8 = avg_vol >= MIN_AVG_VOLUME

        # Price
        cond9 = close >= MIN_PRICE

        # RS line near high or rising
        if 'RS' in df.columns:
            rs = df['RS'].dropna()
            if len(rs) >= 252:
                high_rs = rs.rolling(252).max().iloc[-1]
                cond10 = rs.iloc[-1] > high_rs * 0.90
            else:
                cond10 = True
        else:
            cond10 = True

        if all([cond1, cond2, cond3, cond4, cond5, cond6, cond7, cond8, cond9, cond10]):
            passed.append(ticker)
    return passed
