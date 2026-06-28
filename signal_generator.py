from pattern_detector import detect_vcp, detect_meta_pullback

def generate_entry_signals(watchlist, stock_data):
    """
    For watchlist stocks, detect VCP and META pullback -> BUY signals.
    """
    signals = []
    for ticker in watchlist:
        df = stock_data.get(ticker)
        if df is None:
            continue
        vcp = detect_vcp(df)
        if vcp is None:
            continue
        if vcp['breakout_now']:
            # Buy breakout
            signals.append({
                'ticker': ticker,
                'type': 'BUY_BREAKOUT',
                'price': vcp['close'],
                'stop_loss': vcp['pivot'] * 0.97,
                'reason': f"VCP breakout, pivot={vcp['pivot']:.2f}"
            })
        else:
            meta = detect_meta_pullback(df, vcp['pivot'])
            if meta['is_meta']:
                signals.append({
                    'ticker': ticker,
                    'type': 'BUY_PULLBACK',
                    'price': vcp['close'],
                    'stop_loss': vcp['pivot'] * 0.97,
                    'reason': f"Pullback to BOL, META score={meta['meta_score']}"
                })
    return signals

def generate_exit_signals(positions, stock_data):
    """
    For open positions, check Sell Into Weakness (below 20MA) and simple profit target.
    """
    exits = []
    for pos in positions:
        ticker = pos['ticker']
        df = stock_data.get(ticker)
        if df is None:
            continue
        last = df.iloc[-1]
        close = last['Close']
        # Sell into Weakness: below 20MA
        ma20 = last.get('MA_20')
        if pd.notna(ma20) and close < ma20:
            exits.append({'ticker': ticker, 'type': 'SELL_SIW', 'price': close, 'reason': 'Closed below 20MA'})
            continue
        # Take profit after 20% gain (Sell into Strength simple)
        entry_price = pos['avg_price']
        if close > entry_price * 1.20:
            exits.append({'ticker': ticker, 'type': 'SELL_SIS', 'price': close, 'reason': '20% gain target hit'})
    return exits
