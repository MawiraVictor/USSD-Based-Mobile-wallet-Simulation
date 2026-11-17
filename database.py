import hashlib
import hmac
import os
import secrets
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("WALLET_DB", "wallet.db")

MAX_PIN_ATTEMPTS = 3
PBKDF2_ITERATIONS = 100_000

DEMO_USERS = [("254700000001", 1500.0), ("254700000002", 500.0)]
DEMO_PIN = "1234"


# ---------------------------------------------------------------- connection

@contextmanager
def get_conn():
    # isolation_level=None: we manage transactions explicitly with BEGIN/COMMIT
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction():
    """Yield a connection inside BEGIN IMMEDIATE; commit on success, roll back on error."""
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")


# ---------------------------------------------------------------- PIN hashing

def hash_pin(pin: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return f"{salt}${digest.hex()}"


def _pin_matches(pin: str, stored: str) -> bool:
    if "$" not in stored:  # legacy plain-text PIN from older databases
        return hmac.compare_digest(pin, stored)
    salt, digest = stored.split("$", 1)
    candidate = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return hmac.compare_digest(candidate.hex(), digest)


# ---------------------------------------------------------------- schema

def init_db():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT UNIQUE NOT NULL,
            balance REAL DEFAULT 0,
            pin TEXT NOT NULL,
            failed_attempts INTEGER DEFAULT 0
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
        # Migrate databases created by earlier versions (no failed_attempts column).
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        if "failed_attempts" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN failed_attempts INTEGER DEFAULT 0")

        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO users (phone, balance, pin) VALUES (?, ?, ?)",
                [(phone, bal, hash_pin(DEMO_PIN)) for phone, bal in DEMO_USERS],
            )


# ---------------------------------------------------------------- users

def user_exists(phone: str) -> bool:
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM users WHERE phone=?", (phone,)).fetchone() is not None


def register_user(phone: str, pin: str, balance: float = 0.0) -> bool:
    """Create a user. Returns False if the phone number is already registered."""
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO users (phone, balance, pin) VALUES (?, ?, ?)",
                (phone, balance, hash_pin(pin)),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def get_balance(phone: str) -> float:
    with get_conn() as conn:
        row = conn.execute("SELECT balance FROM users WHERE phone=?", (phone,)).fetchone()
        return row["balance"] if row else 0.0


def is_locked(phone: str) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT failed_attempts FROM users WHERE phone=?", (phone,)).fetchone()
        return row is not None and row["failed_attempts"] >= MAX_PIN_ATTEMPTS


def check_pin(phone: str, pin: str) -> bool:
    """Verify a PIN, counting failures. Locked accounts always fail.

    A successful check resets the failure counter and upgrades legacy
    plain-text PINs to a salted hash.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT pin, failed_attempts FROM users WHERE phone=?", (phone,)
        ).fetchone()
        if row is None or row["failed_attempts"] >= MAX_PIN_ATTEMPTS:
            return False
        if _pin_matches(pin, row["pin"]):
            new_pin = row["pin"] if "$" in row["pin"] else hash_pin(pin)
            conn.execute(
                "UPDATE users SET failed_attempts = 0, pin = ? WHERE phone=?", (new_pin, phone)
            )
            return True
        conn.execute(
            "UPDATE users SET failed_attempts = failed_attempts + 1 WHERE phone=?", (phone,)
        )
        return False


def attempts_left(phone: str) -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT failed_attempts FROM users WHERE phone=?", (phone,)).fetchone()
        return 0 if row is None else max(0, MAX_PIN_ATTEMPTS - row["failed_attempts"])


def change_pin(phone: str, new_pin: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET pin = ?, failed_attempts = 0 WHERE phone=?",
            (hash_pin(new_pin), phone),
        )


# ---------------------------------------------------------------- money

def transfer(sender: str, receiver: str, amount: float) -> bool:
    """Atomic debit + credit + log. Returns True on success."""
    if amount <= 0 or sender == receiver:
        return False
    try:
        with transaction() as conn:
            bal = conn.execute("SELECT balance FROM users WHERE phone=?", (sender,)).fetchone()
            rcv = conn.execute("SELECT 1 FROM users WHERE phone=?", (receiver,)).fetchone()
            if not bal or not rcv or bal["balance"] < amount:
                raise _Abort
            conn.execute("UPDATE users SET balance = balance - ? WHERE phone=?", (amount, sender))
            conn.execute("UPDATE users SET balance = balance + ? WHERE phone=?", (amount, receiver))
            conn.execute(
                "INSERT INTO transactions (sender, receiver, amount, type, status) VALUES (?,?,?,?,?)",
                (sender, receiver, amount, "send_money", "success"),
            )
        return True
    except (_Abort, sqlite3.Error):
        return False


def buy_airtime(phone: str, amount: float) -> bool:
    """Atomically debit the wallet for airtime. Returns True on success."""
    if amount <= 0:
        return False
    try:
        with transaction() as conn:
            bal = conn.execute("SELECT balance FROM users WHERE phone=?", (phone,)).fetchone()
            if not bal or bal["balance"] < amount:
                raise _Abort
            conn.execute("UPDATE users SET balance = balance - ? WHERE phone=?", (amount, phone))
            conn.execute(
                "INSERT INTO transactions (sender, receiver, amount, type, status) VALUES (?,?,?,?,?)",
                (phone, "AIRTIME", amount, "airtime", "success"),
            )
        return True
    except (_Abort, sqlite3.Error):
        return False


def mini_statement(phone: str, limit: int = 5) -> list[sqlite3.Row]:
    """Most recent transactions involving this phone, newest first."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT sender, receiver, amount, type, created_at FROM transactions
               WHERE sender = ? OR receiver = ?
               ORDER BY id DESC LIMIT ?""",
            (phone, phone, limit),
        ).fetchall()


class _Abort(Exception):
    """Internal signal to roll back a transaction without treating it as an error."""
