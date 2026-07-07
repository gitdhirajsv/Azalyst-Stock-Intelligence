import sqlite3
import pandas as pd
from config import DB_PATH
from datetime import datetime
import os

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT,
        action TEXT,
        shares INTEGER,
        price REAL,
        date TEXT,
        reason TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS positions (
        ticker TEXT PRIMARY KEY,
        shares INTEGER,
        avg_price REAL,
        stop_loss REAL
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS cash (
        id INTEGER PRIMARY KEY,
        cash REAL
    )''')
    # Insert starting cash if not exists
    c.execute("SELECT COUNT(*) FROM cash")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO cash (id, cash) VALUES (1, 100000)")  # $100k paper trading
    conn.commit()
    conn.close()

def get_cash():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT cash FROM cash WHERE id=1")
    cash = c.fetchone()[0]
    conn.close()
    return cash

def update_cash(new_cash):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE cash SET cash=? WHERE id=1", (new_cash,))
    conn.commit()
    conn.close()

def get_positions():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("SELECT * FROM positions", conn)
    conn.close()
    return df

def get_trade_history():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("SELECT * FROM trades ORDER BY date DESC", conn)
    conn.close()
    return df

def execute_trade(ticker, action, shares, price, date=None, reason=""):
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    # Do EVERYTHING on a single connection/transaction. The previous version began a
    # write on this connection and then called get_cash()/update_cash() which opened
    # their OWN connections, so the second connection could never acquire the write
    # lock -> "sqlite3.OperationalError: database is locked" on the first BUY.
    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        c = conn.cursor()

        # Read current cash on the SAME connection and validate before writing.
        c.execute("SELECT cash FROM cash WHERE id=1")
        row = c.fetchone()
        cash = row[0] if row else 0
        cost = shares * price
        if action == 'BUY':
            if cash < cost:
                return False, "Insufficient cash"
            new_cash = cash - cost
        elif action == 'SELL':
            new_cash = cash + cost
        else:
            return False, "Invalid action"

        c.execute("INSERT INTO trades (ticker, action, shares, price, date, reason) VALUES (?,?,?,?,?,?)",
                  (ticker, action, shares, price, date, reason))
        c.execute("UPDATE cash SET cash=? WHERE id=1", (new_cash,))

        # Update positions
        if action == 'BUY':
            c.execute("SELECT shares, avg_price FROM positions WHERE ticker=?", (ticker,))
            prow = c.fetchone()
            if prow:
                old_shares, old_avg = prow
                new_shares = old_shares + shares
                new_avg = (old_avg * old_shares + price * shares) / new_shares
                c.execute("UPDATE positions SET shares=?, avg_price=? WHERE ticker=?", (new_shares, new_avg, ticker))
            else:
                c.execute("INSERT INTO positions (ticker, shares, avg_price) VALUES (?,?,?)", (ticker, shares, price))
        elif action == 'SELL':
            c.execute("SELECT shares FROM positions WHERE ticker=?", (ticker,))
            prow = c.fetchone()
            if prow:
                remaining = prow[0] - shares
                if remaining <= 0:
                    c.execute("DELETE FROM positions WHERE ticker=?", (ticker,))
                else:
                    c.execute("UPDATE positions SET shares=? WHERE ticker=?", (remaining, ticker))

        conn.commit()
        return True, "Success"
    finally:
        conn.close()
