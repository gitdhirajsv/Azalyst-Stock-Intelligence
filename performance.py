"""
Azalyst Stock Intelligence — Performance Analytics Module

Computes portfolio-level performance metrics per CFA L3 curriculum
(Portfolio Performance Evaluation) and Minervini's trading journal metrics.

Metrics computed:
  - Sharpe Ratio, Sortino Ratio, Calmar Ratio
  - Maximum Drawdown & Drawdown Duration
  - Win Rate, Profit Factor, Average R-Multiple, Expectancy
  - Value at Risk (VaR) and Conditional VaR (Expected Shortfall)
"""
import numpy as np
import pandas as pd
from paper_trader import get_trade_history, get_equity_snapshots


# ---------- Risk-free rate proxy (annualised, conservative) ----------
RISK_FREE_RATE = 0.045   # ~4.5% (current T-bill approx)
TRADING_DAYS_PER_YEAR = 252


# =====================================================================
#  EQUITY-CURVE BASED METRICS
# =====================================================================

def _daily_returns(equity_series):
    """Simple daily returns from an equity Series (indexed by date)."""
    return equity_series.pct_change().dropna()


def sharpe_ratio(equity_series, rf=RISK_FREE_RATE):
    """Annualised Sharpe ratio: (R_p - R_f) / σ_p."""
    dr = _daily_returns(equity_series)
    if dr.empty or dr.std() == 0:
        return 0.0
    daily_rf = (1 + rf) ** (1 / TRADING_DAYS_PER_YEAR) - 1
    excess = dr - daily_rf
    return float((excess.mean() / excess.std()) * np.sqrt(TRADING_DAYS_PER_YEAR))


def sortino_ratio(equity_series, rf=RISK_FREE_RATE):
    """Annualised Sortino ratio: (R_p - R_f) / σ_downside."""
    dr = _daily_returns(equity_series)
    if dr.empty:
        return 0.0
    daily_rf = (1 + rf) ** (1 / TRADING_DAYS_PER_YEAR) - 1
    excess = dr - daily_rf
    downside = excess[excess < 0]
    if downside.empty or downside.std() == 0:
        return float('inf') if excess.mean() > 0 else 0.0
    return float((excess.mean() / downside.std()) * np.sqrt(TRADING_DAYS_PER_YEAR))


def max_drawdown(equity_series):
    """Maximum peak-to-trough drawdown (as a negative fraction, e.g. -0.15 = -15%).

    Also returns drawdown duration in trading days.
    """
    if equity_series.empty:
        return 0.0, 0
    running_max = equity_series.cummax()
    drawdowns = (equity_series - running_max) / running_max
    mdd = float(drawdowns.min())

    # Duration: longest streak below previous peak
    is_drawdown = drawdowns < 0
    groups = (~is_drawdown).cumsum()
    if is_drawdown.any():
        durations = is_drawdown.groupby(groups).sum()
        max_duration = int(durations.max())
    else:
        max_duration = 0
    return mdd, max_duration


def calmar_ratio(equity_series, rf=RISK_FREE_RATE):
    """Annualised return / |max drawdown|."""
    dr = _daily_returns(equity_series)
    if dr.empty:
        return 0.0
    ann_return = dr.mean() * TRADING_DAYS_PER_YEAR
    mdd, _ = max_drawdown(equity_series)
    if mdd == 0:
        return float('inf') if ann_return > 0 else 0.0
    return float((ann_return - rf) / abs(mdd))


# =====================================================================
#  VALUE AT RISK (VaR) & CONDITIONAL VaR (CVaR / Expected Shortfall)
# =====================================================================

def historical_var(equity_series, confidence=0.95):
    """Historical VaR at the given confidence level.

    Returns the loss threshold: e.g. at 95%, on 95% of days the portfolio
    should not lose more than this amount (as a positive fraction of equity).
    """
    dr = _daily_returns(equity_series)
    if dr.empty:
        return 0.0
    return float(-np.percentile(dr, (1 - confidence) * 100))


def conditional_var(equity_series, confidence=0.95):
    """CVaR (Expected Shortfall): average loss in the worst (1-confidence) days."""
    dr = _daily_returns(equity_series)
    if dr.empty:
        return 0.0
    threshold = np.percentile(dr, (1 - confidence) * 100)
    tail = dr[dr <= threshold]
    if tail.empty:
        return 0.0
    return float(-tail.mean())


# =====================================================================
#  TRADE-LEVEL METRICS (from closed trade history)
# =====================================================================

def _closed_trade_pnl(trades_df):
    """Match BUY/SELL pairs per ticker (FIFO) and compute per-trade P&L.

    Returns a list of dicts: [{ticker, buy_price, sell_price, shares, pnl, r_multiple}, ...]
    """
    if trades_df is None or trades_df.empty:
        return []

    results = []
    # Group by ticker, process FIFO
    for ticker, group in trades_df.groupby('ticker'):
        buys = []
        for _, row in group.sort_values('date').iterrows():
            if row['action'] == 'BUY':
                buys.append({'price': row['price'], 'shares': row['shares']})
            elif row['action'] == 'SELL' and buys:
                sell_shares = row['shares']
                sell_price = row['price']
                while sell_shares > 0 and buys:
                    buy = buys[0]
                    matched = min(sell_shares, buy['shares'])
                    pnl = (sell_price - buy['price']) * matched
                    # R-multiple: profit / initial risk (approximate as 3% of buy price
                    # if we don't have the original stop — in practice the stop is
                    # recorded in the position table, but for closed trades we
                    # approximate conservatively)
                    initial_risk_per_share = buy['price'] * 0.03
                    r_multiple = pnl / (initial_risk_per_share * matched) if initial_risk_per_share > 0 else 0
                    results.append({
                        'ticker': ticker,
                        'buy_price': buy['price'],
                        'sell_price': sell_price,
                        'shares': matched,
                        'pnl': pnl,
                        'r_multiple': r_multiple,
                    })
                    buy['shares'] -= matched
                    sell_shares -= matched
                    if buy['shares'] <= 0:
                        buys.pop(0)
    return results


def win_rate(trades_df):
    """Percentage of closed trades that were profitable."""
    closed = _closed_trade_pnl(trades_df)
    if not closed:
        return 0.0
    winners = sum(1 for t in closed if t['pnl'] > 0)
    return winners / len(closed)


def profit_factor(trades_df):
    """Gross profit / Gross loss."""
    closed = _closed_trade_pnl(trades_df)
    if not closed:
        return 0.0
    gross_profit = sum(t['pnl'] for t in closed if t['pnl'] > 0)
    gross_loss = abs(sum(t['pnl'] for t in closed if t['pnl'] < 0))
    if gross_loss == 0:
        return float('inf') if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def avg_r_multiple(trades_df):
    """Average R-multiple across all closed trades."""
    closed = _closed_trade_pnl(trades_df)
    if not closed:
        return 0.0
    return float(np.mean([t['r_multiple'] for t in closed]))


def expectancy(trades_df):
    """Expectancy = (Win% × Avg Win) - (Loss% × Avg Loss)."""
    closed = _closed_trade_pnl(trades_df)
    if not closed:
        return 0.0
    winners = [t['pnl'] for t in closed if t['pnl'] > 0]
    losers = [t['pnl'] for t in closed if t['pnl'] <= 0]
    total = len(closed)
    avg_win = np.mean(winners) if winners else 0
    avg_loss = abs(np.mean(losers)) if losers else 0
    win_pct = len(winners) / total
    loss_pct = len(losers) / total
    return float(win_pct * avg_win - loss_pct * avg_loss)


# =====================================================================
#  AGGREGATED REPORT
# =====================================================================

def generate_performance_report():
    """Generate a full performance report dict from the paper trading DB."""
    snapshots = get_equity_snapshots()
    trades = get_trade_history()

    report = {
        'has_data': False,
        'equity_metrics': {},
        'risk_metrics': {},
        'trade_metrics': {},
    }

    if snapshots is not None and not snapshots.empty and 'total_equity' in snapshots.columns:
        equity = pd.Series(
            snapshots['total_equity'].values,
            index=pd.to_datetime(snapshots['date']),
        )
        if len(equity) >= 2:
            report['has_data'] = True
            mdd, mdd_dur = max_drawdown(equity)
            report['equity_metrics'] = {
                'sharpe_ratio': round(sharpe_ratio(equity), 3),
                'sortino_ratio': round(sortino_ratio(equity), 3),
                'calmar_ratio': round(calmar_ratio(equity), 3),
                'max_drawdown_pct': round(mdd * 100, 2),
                'max_drawdown_duration_days': mdd_dur,
                'total_return_pct': round(
                    (equity.iloc[-1] / equity.iloc[0] - 1) * 100, 2
                ),
            }
            report['risk_metrics'] = {
                'var_95_daily_pct': round(historical_var(equity, 0.95) * 100, 3),
                'var_99_daily_pct': round(historical_var(equity, 0.99) * 100, 3),
                'cvar_95_daily_pct': round(conditional_var(equity, 0.95) * 100, 3),
            }

    if trades is not None and not trades.empty:
        report['has_data'] = True
        closed = _closed_trade_pnl(trades)
        report['trade_metrics'] = {
            'total_closed_trades': len(closed),
            'win_rate_pct': round(win_rate(trades) * 100, 1),
            'profit_factor': round(profit_factor(trades), 2),
            'avg_r_multiple': round(avg_r_multiple(trades), 2),
            'expectancy_dollars': round(expectancy(trades), 2),
        }

    return report
