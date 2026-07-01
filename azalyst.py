import os
from datetime import datetime
from config import *
from universe import get_universe
from data_loader import load_stock_data
from utils import fetch_historical
from market_regime import detect_bull_regime
from stock_screener import apply_stage2_screen
from signal_generator import generate_entry_signals
from risk_manager import RiskManager
from paper_trader import init_db, get_cash, get_positions, execute_trade

def run_pipeline():
    print(f"[{datetime.now().isoformat()}] Starting Azalyst Stock Intelligence Pipeline...")
    init_db()

    # Load Universe
    tickers = get_universe()
    print(f"Loaded {len(tickers)} tickers from universe.")

    # Load Data
    stock_data, _ = load_stock_data(tickers)
    
    # Market Regime
    benchmark_df = fetch_historical(BENCHMARK_TICKER, "2y")
    if benchmark_df is None or benchmark_df.empty:
        print("Failed to fetch benchmark data.")
        return
        
    regime = detect_bull_regime(benchmark_df)
    is_bull = regime.get('is_bull', False)
    print(f"Market Regime: {'BULL' if is_bull else 'BEAR'}")
    
    # Screener
    stage2_passed = apply_stage2_screen(stock_data)
    print(f"Tickers passing Stage 2: {len(stage2_passed)}")
    
    # Signals
    signals = generate_entry_signals(stage2_passed, stock_data)
    print(f"Entry Signals Generated: {len(signals)}")
    
    # Trading Logic
    cash = get_cash()
    positions = get_positions()
    current_equity = cash
    
    # Calculate current equity including open positions
    if not positions.empty:
        for idx, pos in positions.iterrows():
            ticker = pos['ticker']
            shares = pos['shares']
            if ticker in stock_data:
                current_price = stock_data[ticker]['Close'].iloc[-1]
                current_equity += shares * current_price
    
    rm = RiskManager(current_equity)
    
    # Evaluate Exits (Stop Loss)
    if not positions.empty:
        for idx, pos in positions.iterrows():
            ticker = pos['ticker']
            shares = pos['shares']
            avg_price = pos['avg_price']
            if ticker in stock_data:
                current_price = stock_data[ticker]['Close'].iloc[-1]
                # Dummy exit rule: Exit if price drops 8% below avg_price
                if current_price < avg_price * 0.92:
                    execute_trade(ticker, 'SELL', shares, current_price, reason="Stop Loss")
                    print(f"SELL (Stop Loss) {shares} {ticker} @ {current_price}")
    
    # Execute Entries
    # Only buy if in Bull Regime and we have signals
    if is_bull and signals:
        for sig in signals:
            ticker = sig['ticker']
            price = sig['price']
            
            # Check if we already hold it
            if not positions.empty and ticker in positions['ticker'].values:
                continue
                
            allowed, shares, reason = rm.check_entry(price, sig['stop_loss'])
            if allowed:
                success, msg = execute_trade(ticker, 'BUY', shares, price, reason=sig['reason'])
                if success:
                    print(f"BUY {shares} {ticker} @ {price} ({sig['reason']})")
                    # Update RM equity
                    rm.update_equity(get_cash()) # Approximation

    print(f"[{datetime.now().isoformat()}] Pipeline execution complete.")

if __name__ == "__main__":
    run_pipeline()
