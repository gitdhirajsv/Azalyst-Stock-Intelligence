import streamlit as st
import pandas as pd
from datetime import datetime
from config import *
from universe import get_universe
from data_loader import load_stock_data
from market_regime import detect_bull_regime
from stock_screener import apply_stage2_screen
from signal_generator import generate_entry_signals, generate_exit_signals
from risk_manager import RiskManager
from paper_trader import (
    init_db, execute_trade, get_positions, get_trade_history, get_cash
)

st.set_page_config(page_title="Azalyst Stock Intelligence", layout="wide")
st.markdown("""
<style>
.metric-card { background-color: #1a1a2e; padding: 1rem; border-radius: 0.5rem; border: 1px solid #2d2d44; text-align: center; }
.bull { color: #00ff88; } .bear { color: #ff4444; }
</style>
""", unsafe_allow_html=True)

init_db()

# ---------------------------
# SIDEBAR
# ---------------------------
st.sidebar.title("🔍 Azalyst Stock Intelligence")
mode = st.sidebar.radio("Navigation", ["Market Overview", "Screener", "Signals", "Positions", "Trade Log", "Execute Trades"])

# Load data (cached)
@st.cache_data(ttl=3600)
def load_all():
    tickers = get_universe()
    stock_data, benchmark_df = load_stock_data(tickers)
    return tickers, stock_data, benchmark_df

tickers, stock_data, benchmark_df = load_all()
regime_signals, is_bull = detect_bull_regime(benchmark_df)

# Current equity = cash + value of open positions (approx)
cash = get_cash()
positions_df = get_positions()
if not positions_df.empty:
    # compute market value of each position
    total_market_val = 0
    for _, row in positions_df.iterrows():
        tk = row['ticker']
        if tk in stock_data:
            current_price = stock_data[tk].iloc[-1]['Close']
            total_market_val += current_price * row['shares']
    equity = cash + total_market_val
else:
    equity = cash

# Risk manager
risk_mgr = RiskManager(equity)

# ---------------------------
# PAGE: Market Overview
# ---------------------------
if mode == "Market Overview":
    st.title("🌐 Market Overview")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(f"<div class='metric-card'><h3>Market Regime</h3><h2 class='{'bull' if is_bull else 'bear'}'>{'BULL' if is_bull else 'BEAR'}</h2></div>", unsafe_allow_html=True)
    with col2:
        st.markdown(f"<div class='metric-card'><h3>S&P 500 Last Close</h3><h2>{benchmark_df['Close'].iloc[-1]:.2f}</h2></div>", unsafe_allow_html=True)
    with col3:
        st.markdown(f"<div class='metric-card'><h3>Golden Cross</h3><h2>{'YES' if regime_signals.get('golden_cross',False) else 'NO'}</h2></div>", unsafe_allow_html=True)
    
    st.subheader("Benchmark Chart")
    st.line_chart(benchmark_df['Close'][-252:], use_container_width=True)
    st.subheader("Regime Signals Details")
    st.json(regime_signals)

# ---------------------------
# PAGE: Screener
# ---------------------------
elif mode == "Screener":
    st.title("🔎 Stage 2 Screener")
    if st.button("Run Screen"):
        with st.spinner("Screening..."):
            watchlist = apply_stage2_screen(stock_data)
            st.success(f"Found {len(watchlist)} stocks")
            st.dataframe(pd.DataFrame(watchlist, columns=["Ticker"]))
            if watchlist:
                st.download_button("Download Watchlist", "\n".join(watchlist), file_name="watchlist.txt")
    else:
        st.info("Click 'Run Screen' to find Stage 2 stocks.")

# ---------------------------
# PAGE: Signals
# ---------------------------
elif mode == "Signals":
    st.title("📡 Entry & Exit Signals")
    watchlist = st.session_state.get('watchlist', [])
    if not watchlist:
        # if no watchlist yet, run screen automatically?
        watchlist = apply_stage2_screen(stock_data)
        st.session_state['watchlist'] = watchlist
    st.write(f"Analyzing {len(watchlist)} Stage 2 stocks...")
    entry_signals = generate_entry_signals(watchlist, stock_data)
    exit_signals = generate_exit_signals(positions_df.to_dict('records') if not positions_df.empty else [], stock_data)

    tab1, tab2 = st.tabs(["Entry Signals", "Exit Signals"])
    with tab1:
        if entry_signals:
            st.dataframe(pd.DataFrame(entry_signals))
        else:
            st.write("No entry signals today.")
    with tab2:
        if exit_signals:
            st.dataframe(pd.DataFrame(exit_signals))
        else:
            st.write("No exit signals.")
            
    if st.button("Execute All Signals (Paper)"):
        results = []
        for sig in entry_signals:
            shares = risk_mgr.position_size(sig['price'], sig['stop_loss'])
            if shares > 0:
                success, msg = execute_trade(sig['ticker'], 'BUY', shares, sig['price'], reason=sig['reason'])
                results.append((sig['ticker'], 'BUY', shares, success, msg))
        for sig in exit_signals:
            pos_row = positions_df[positions_df['ticker']==sig['ticker']]
            if not pos_row.empty:
                shares = pos_row.iloc[0]['shares']
                success, msg = execute_trade(sig['ticker'], 'SELL', shares, sig['price'], reason=sig['reason'])
                results.append((sig['ticker'], 'SELL', shares, success, msg))
        st.write("Execution Results:")
        st.dataframe(pd.DataFrame(results, columns=["Ticker","Action","Shares","Success","Message"]))

# ---------------------------
# PAGE: Positions
# ---------------------------
elif mode == "Positions":
    st.title("💼 Open Positions")
    if not positions_df.empty:
        # enrich with current price and P&L
        data = []
        for _, row in positions_df.iterrows():
            tk = row['ticker']
            if tk in stock_data:
                cur_price = stock_data[tk].iloc[-1]['Close']
                market_val = cur_price * row['shares']
                cost = row['avg_price'] * row['shares']
                pnl = market_val - cost
                pnl_pct = (pnl / cost) * 100 if cost != 0 else 0
                data.append([tk, row['shares'], round(row['avg_price'],2), round(cur_price,2), round(market_val,2), round(pnl,2), round(pnl_pct,2)])
        if data:
            st.dataframe(pd.DataFrame(data, columns=["Ticker","Shares","Avg Price","Current Price","Market Value","P&L","P&L%"]))
        else:
            st.write("No open positions.")
    else:
        st.write("No open positions.")
    st.metric("Cash Balance", f"${cash:,.2f}")
    st.metric("Total Equity", f"${equity:,.2f}")

# ---------------------------
# PAGE: Trade Log
# ---------------------------
elif mode == "Trade Log":
    st.title("📜 Trade History")
    trades_df = get_trade_history()
    if not trades_df.empty:
        st.dataframe(trades_df)
    else:
        st.write("No trades yet.")

# ---------------------------
# PAGE: Execute Trades (Manual)
# ---------------------------
elif mode == "Execute Trades":
    st.title("🛠️ Manual Trade Entry")
    ticker = st.text_input("Ticker")
    action = st.selectbox("Action", ["BUY","SELL"])
    shares = st.number_input("Shares", min_value=1, value=10)
    price = st.number_input("Price", value=0.0)
    reason = st.text_input("Reason")
    if st.button("Execute"):
        success, msg = execute_trade(ticker, action, shares, price, reason=reason)
        if success:
            st.success(f"Trade executed: {msg}")
        else:
            st.error(msg)
