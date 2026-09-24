import sqlite3
import pandas as pd
from config import DB_PATH
from datetime import datetime
import os

def _connect(timeout=None):
    """Open the ledger, creating its parent directory if needed.

    DB_PATH is relative, so any caller running from a different cwd (tests
    use monkeypatch.chdir) would otherwise fail with "unable to open
    database file".
    """
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    if timeout is None:
        return sqlite3.connect(DB_PATH)
    return sqlite3.connect(DB_PATH, timeout=timeout)

def init_db():
    conn = _connect()
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
    c.execute('''CREATE TABLE IF NOT EXISTS equity_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT,
        cash REAL,
        positions_value REAL,
        total_equity REAL
    )''')
    # Insert starting cash if not exists
    c.execute("SELECT COUNT(*) FROM cash")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO cash (id, cash) VALUES (1, 100000)")  # $100k paper trading

    # Migration: positions predates source/sector/rs_rating tracking. Add the columns
    # if missing so existing DBs (now that they actually persist) don't need a wipe.
    existing_cols = {row[1] for row in c.execute("PRAGMA table_info(positions)").fetchall()}
    for col, coltype in (
        ("source", "TEXT"), ("sector", "TEXT"), ("rs_rating", "INTEGER"),
        # STK-09 (alpha post-mortem 2026-09-01): the daily bar an entry was
        # taken against. Needed so the stop engine can skip the entry bar's
        # own Low (price action that happened BEFORE the fill) and so the
        # entry loop can refuse to act twice on the same completed bar.
        ("entry_bar_date", "TEXT"),
    ):
        if col not in existing_cols:
            c.execute(f"ALTER TABLE positions ADD COLUMN {col} {coltype}")

    # STK-09: same migration for trades — bar_date records which completed
    # daily bar a trade was decided on (the `date` column is the RUN date,
    # which with hourly/weekend crons can be days after the bar).
    trade_cols = {row[1] for row in c.execute("PRAGMA table_info(trades)").fetchall()}
    if "bar_date" not in trade_cols:
        c.execute("ALTER TABLE trades ADD COLUMN bar_date TEXT")

    conn.commit()
    conn.close()

def get_cash():
    conn = _connect()
    c = conn.cursor()
    c.execute("SELECT cash FROM cash WHERE id=1")
    cash = c.fetchone()[0]
    conn.close()
    return cash

def update_cash(new_cash):
    conn = _connect()
    c = conn.cursor()
    c.execute("UPDATE cash SET cash=? WHERE id=1", (new_cash,))
    conn.commit()
    conn.close()

def get_positions():
    conn = _connect()
    df = pd.read_sql("SELECT * FROM positions", conn)
    conn.close()
    return df

def get_trade_history():
    conn = _connect()
    df = pd.read_sql("SELECT * FROM trades ORDER BY date DESC", conn)
    conn.close()
    return df

def record_equity_snapshot(cash, positions_value, date=None):
    """Record a point on the equity curve for performance analytics.

    Called once per pipeline run from azalyst.py after computing total equity.
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    total_equity = cash + positions_value
    conn = _connect(timeout=30)
    try:
        c = conn.cursor()
        # Guard against callers that skip init_db() (e.g. tests that patch
        # it out): this table must exist independent of that call.
        c.execute('''CREATE TABLE IF NOT EXISTS equity_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            cash REAL,
            positions_value REAL,
            total_equity REAL
        )''')
        # Avoid duplicate snapshots for the same date (idempotent reruns)
        c.execute("DELETE FROM equity_snapshots WHERE date = ?", (date,))
        c.execute(
            "INSERT INTO equity_snapshots (date, cash, positions_value, total_equity) "
            "VALUES (?, ?, ?, ?)",
            (date, cash, positions_value, total_equity),
        )
        conn.commit()
    finally:
        conn.close()


def get_equity_snapshots():
    """Return all equity snapshots as a DataFrame, ordered by date."""
    conn = _connect()
    df = pd.read_sql("SELECT * FROM equity_snapshots ORDER BY date", conn)
    conn.close()
    return df


def last_trade_bar_date(ticker):
    """Latest bar a trade for `ticker` was decided on, or None if never traded.

    STK-09 remediation (alpha post-mortem 2026-09-01): the only re-entry
    guard used to be `if ticker in held_tickers` — vacated the instant a
    stop-out SELL removed the position. With up to 5 entry-eligible cron
    runs per completed daily bar (plus weekend runs), a still-valid signal
    on the same stale bar produced a buy -> stop -> rebuy loop: the live
    book bought and stopped out RHI FOUR times at identical prices over one
    weekend (Aug 29-31), -2% each time, 66% of all realized losses. The
    entry loop now refuses to act on a ticker whose latest recorded trade
    was decided on (or after) the bar currently being evaluated.

    COALESCE falls back to the run `date` for legacy rows with no bar_date:
    a run date is >= its bar date, so the fallback can only be MORE
    conservative (block, never allow) — exactly the right failure mode.
    """
    conn = _connect()
    try:
        c = conn.cursor()
        c.execute(
            "SELECT MAX(COALESCE(bar_date, date)) FROM trades WHERE ticker=?",
            (ticker,),
        )
        row = c.fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        # trades table absent (fresh DB before init_db, or a test that stubs
        # init_db out): no history means nothing to latch on.
        return None
    finally:
        conn.close()


def execute_trade(ticker, action, shares, price, date=None, reason="", source=None, sector=None, rs_rating=None, stop_loss=None, bar_date=None):
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    # Do EVERYTHING on a single connection/transaction. The previous version began a
    # write on this connection and then called get_cash()/update_cash() which opened
    # their OWN connections, so the second connection could never acquire the write
    # lock -> "sqlite3.OperationalError: database is locked" on the first BUY.
    conn = _connect(timeout=30)
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

        c.execute("INSERT INTO trades (ticker, action, shares, price, date, reason, bar_date) VALUES (?,?,?,?,?,?,?)",
                  (ticker, action, shares, price, date, reason, bar_date))
        c.execute("UPDATE cash SET cash=? WHERE id=1", (new_cash,))

        # Update positions
        if action == 'BUY':
            c.execute("SELECT shares, avg_price, stop_loss FROM positions WHERE ticker=?", (ticker,))
            prow = c.fetchone()
            if prow:
                old_shares, old_avg, old_stop = prow
                new_shares = old_shares + shares
                new_avg = (old_avg * old_shares + price * shares) / new_shares
                # Adding to an existing position: keep the original entry's source/sector/
                # rs_rating (it's still the same holding), just update size/cost basis.
                # STK-01: keep the ORIGINAL stop too -- only fill it in if the existing
                # position somehow has none (e.g. a pre-migration row), never overwrite
                # a real stop with the top-up signal's own stop.
                new_stop = old_stop if old_stop is not None else stop_loss
                c.execute(
                    "UPDATE positions SET shares=?, avg_price=?, stop_loss=? WHERE ticker=?",
                    (new_shares, new_avg, new_stop, ticker),
                )
            else:
                c.execute(
                    "INSERT INTO positions (ticker, shares, avg_price, source, sector, rs_rating, stop_loss, entry_bar_date) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (ticker, shares, price, source, sector, rs_rating, stop_loss, bar_date),
                )
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
