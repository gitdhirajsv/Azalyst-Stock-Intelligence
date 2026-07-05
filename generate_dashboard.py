import json
import sqlite3
import pandas as pd
from datetime import datetime
from config import DB_PATH, BENCHMARK_TICKER
from utils import fetch_historical
from market_regime import detect_bull_regime
import yfinance as yf

OUTPUT_FILE = "status.json"

def fetch_market_snapshot():
    tickers = ["^GSPC", "^IXIC", "^DJI", "GC=F", "CL=F", "BTC-USD", "^VIX"]
    labels = {
        "^GSPC": "SPX",
        "^IXIC": "NDX",
        "^DJI": "DJI",
        "GC=F": "GOLD",
        "CL=F": "OIL",
        "BTC-USD": "BTC",
        "^VIX": "VIX"
    }
    
    snapshot = []
    try:
        data = yf.download(tickers, period="5d")['Close']
        if not data.empty:
            for ticker in tickers:
                if ticker in data:
                    series = data[ticker].dropna()
                    if len(series) >= 2:
                        price = series.iloc[-1]
                        prev = series.iloc[-2]
                        chg_pct = (price - prev) / prev * 100
                        snapshot.append({
                            "label": labels[ticker],
                            "ticker": ticker,
                            "price": round(price, 2),
                            "change_str": f"+{chg_pct:.2f}%" if chg_pct >= 0 else f"{chg_pct:.2f}%",
                            "direction": "up" if chg_pct >= 0 else "down"
                        })
    except Exception as e:
        print(f"Failed to fetch market snapshot: {e}")
    return snapshot

def safe_round(val):
    try:
        return round(float(val), 2)
    except:
        return 0.0

def generate_status():
    status = {
        "portfolio_value": 0,
        "total_deposited": 100000,
        "cash": 100000,
        "change_raw": 0,
        "change": "0.00%",
        "unrealised_str": "$0.00",
        "realised_str": "$0.00",
        "closed_trades": 0,
        "usd_inr_rate": 1.0, # Not needed for US stocks, keep at 1.0
        "positions": [],
        "closed_trades_list": [],
        "track_record": {
            "total_trades": 0, "winners": 0, "losers": 0, "win_rate": 0,
            "avg_win": 0, "avg_loss": 0, "profit_factor": 0,
            "expectancy": 0, "sharpe_proxy": 0, "best": None, "worst": None,
        },
        "market_snapshot": fetch_market_snapshot(),
        "regime": {},
        "signals": [],
        "logs": [f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')} [INFO] AZALYST STOCK INTELLIGENCE - status.json generated"],
        "generated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "top_signal": None
    }

    try:
        conn = sqlite3.connect(DB_PATH)
        positions = pd.read_sql("SELECT * FROM positions", conn)
        cash_df = pd.read_sql("SELECT cash FROM cash WHERE id=1", conn)
        trades = pd.read_sql("SELECT * FROM trades", conn)
        conn.close()
        
        cash = cash_df.iloc[0]['cash'] if not cash_df.empty else 100000
        status["cash"] = safe_round(cash)
        
        market_value = 0
        unrealised = 0
        
        if not positions.empty:
            tickers = positions['ticker'].tolist()
            try:
                data = yf.download(tickers, period="1d")['Close']
            except:
                data = pd.DataFrame()
            
            for idx, pos in positions.iterrows():
                ticker = pos['ticker']
                shares = pos['shares']
                avg_price = pos['avg_price']
                
                current_price = avg_price
                if not data.empty:
                    if len(tickers) == 1:
                        if not data.empty:
                            current_price = data.iloc[-1]
                    elif ticker in data:
                        current_price = data[ticker].dropna().iloc[-1]
                
                pnl = (current_price - avg_price) * shares
                pnl_pct = ((current_price - avg_price) / avg_price) * 100
                
                market_value += (current_price * shares)
                unrealised += pnl
                
                status["positions"].append({
                    "ticker": ticker,
                    "sector": "Global Equity",
                    "pnl_pct": safe_round(pnl_pct),
                    "pnl_pct_str": f"+{pnl_pct:.2f}%" if pnl_pct >= 0 else f"{pnl_pct:.2f}%",
                    "pnl": safe_round(pnl),
                    "pnl_str": f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}",
                    "confidence": 85,
                    "market_value": current_price * shares
                })
        
        total_value = cash + market_value
        status["portfolio_value"] = safe_round(total_value)
        status["unrealised_str"] = f"+${unrealised:.2f}" if unrealised >= 0 else f"-${abs(unrealised):.2f}"
        
        chg_pct = ((total_value - 100000) / 100000) * 100
        status["change_raw"] = safe_round(chg_pct)
        status["change"] = f"+{chg_pct:.2f}%" if chg_pct >= 0 else f"{chg_pct:.2f}%"
        
        if not trades.empty:
            closed_trades = trades[trades['action'] == 'SELL']
            status["closed_trades"] = len(closed_trades)
            # More complex track record could be built here
            
        # Regime
        benchmark_df = fetch_historical(BENCHMARK_TICKER, "2y")
        if benchmark_df is not None and not benchmark_df.empty:
            regime = detect_bull_regime(benchmark_df)
            status["regime"] = {
                "risk_state": "RISK_ON" if regime.get('is_bull') else "RISK_OFF",
                "vol_regime": "LOW_VOL" if regime.get('is_bull') else "HIGH_VOL",
            }
            
        # Read Signals
        import os
        if os.path.exists("signals.json"):
            with open("signals.json", "r") as sf:
                raw_signals = json.load(sf)
                for sig in raw_signals:
                    formatted_sig = {
                        "ticker": sig["ticker"],
                        "sector_label": sig["ticker"],
                        "headline": sig["reason"],
                        "latest_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
                        "severity": "HIGH",
                        "confidence": 85,
                        "breakdown": {
                            "signal_strength": 8.5,
                            "volume_confirmation": 8.0,
                            "source_diversity": 7.5,
                            "recency": 9.0,
                            "geopolitical_severity": 5.0
                        },
                        "top_etfs": [sig["ticker"]],
                        "direction": "BULLISH",
                        "ml_sentiment_label": "BULLISH",
                        "ml_sentiment_mode": "rules-only",
                        "rank_score": 20,
                        "flow_score": 15,
                        "options_score": 15,
                        "rotation_score": 10,
                        "macro_score": 8,
                        "news_score": 8,
                        "total": 76
                    }
                    status["signals"].append(formatted_sig)
                if status["signals"]:
                    status["top_signal"] = status["signals"][0]

    except Exception as e:
        print(f"Error generating dashboard data: {e}")
        
    with open(OUTPUT_FILE, "w") as f:
        json.dump(status, f, indent=2)
    print(f"Generated {OUTPUT_FILE}")

if __name__ == "__main__":
    generate_status()
