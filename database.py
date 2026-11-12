import sqlite3
from contextlib import contextmanager

DB_PATH = "wallet.db"

def init_db():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT UNIQUE NOT NULL,
            balance REAL DEFAULT 0,
            pin TEXT DEFAULT '1234'
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT,
            receiver TEXT,
            amount REAL,
            type TEXT,
            status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        # seed demo users
        cur = conn.execute("SELECT COUNT(*) FROM users")
        if cur.fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO users (phone, balance) VALUES (?, ?)",
                [("254700000001", 1500.0), ("254700000002", 500.0)]
            )

@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, isolation_level=None)  # autocommit off via BEGIN
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def get_balance(phone: str) -> float:
    with get_conn() as conn:
        row = conn.execute("SELECT balance FROM users WHERE phone=?", (phone,)).fetchone()
        return row["balance"] if row else 0.0

def user_exists(phone: str) -> bool:
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM users WHERE phone=?", (phone,)).fetchone() is not None

def check_pin(phone: str, pin: str) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT pin FROM users WHERE phone=?", (phone,)).fetchone()
        return row is not None and row["pin"] == pin

def transfer(sender: str, receiver: str, amount: float) -> bool:
    """Atomic debit+credit+log. Returns True on success."""
    if amount <= 0:
        return False
    with get_conn() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            bal = conn.execute("SELECT balance FROM users WHERE phone=?", (sender,)).fetchone()
            if not bal or bal["balance"] < amount:
                conn.execute("ROLLBACK")
                return False
            conn.execute("UPDATE users SET balance = balance - ? WHERE phone=?", (amount, sender))
            conn.execute("UPDATE users SET balance = balance + ? WHERE phone=?", (amount, receiver))
            conn.execute(
                "INSERT INTO transactions (sender, receiver, amount, type, status) VALUES (?,?,?,?,?)",
                (sender, receiver, amount, "send_money", "success")
            )
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            return False