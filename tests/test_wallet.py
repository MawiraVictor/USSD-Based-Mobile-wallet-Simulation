import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
from main import app  # noqa: E402

ALICE = "254700000001"  # KES 1500
BOB = "254700000002"    # KES 500
PIN = "1234"


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def ussd(client, text, phone=ALICE, session="s1"):
    r = client.post("/ussd", data={"sessionId": session, "phoneNumber": phone, "text": text})
    assert r.status_code == 200
    return r.text


# ---------------------------------------------------------------- database

def test_seeded_users_have_hashed_pins():
    with database.get_conn() as conn:
        pins = [r["pin"] for r in conn.execute("SELECT pin FROM users")]
    assert len(pins) == 2
    assert all("$" in p and PIN not in p for p in pins)
    assert database.check_pin(ALICE, PIN)


def test_legacy_plaintext_pin_is_accepted_and_upgraded():
    with database.get_conn() as conn:
        conn.execute("UPDATE users SET pin='4321' WHERE phone=?", (ALICE,))
    assert database.check_pin(ALICE, "4321")
    with database.get_conn() as conn:
        stored = conn.execute("SELECT pin FROM users WHERE phone=?", (ALICE,)).fetchone()["pin"]
    assert "$" in stored
    assert database.check_pin(ALICE, "4321")


def test_migrates_old_schema_without_failed_attempts(tmp_path, monkeypatch):
    import sqlite3
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, phone TEXT UNIQUE NOT NULL,
                            balance REAL DEFAULT 0, pin TEXT DEFAULT '1234');
        INSERT INTO users (phone, balance) VALUES ('254711111111', 100);
    """)
    conn.close()
    monkeypatch.setattr(database, "DB_PATH", str(path))
    database.init_db()
    assert database.check_pin("254711111111", "1234")
    assert database.get_balance("254711111111") == 100


def test_transfer_moves_money_and_logs():
    assert database.transfer(ALICE, BOB, 200)
    assert database.get_balance(ALICE) == 1300
    assert database.get_balance(BOB) == 700
    assert len(database.mini_statement(ALICE)) == 1


@pytest.mark.parametrize("receiver, amount", [
    (BOB, 5000),            # insufficient funds
    (BOB, 0),               # non-positive
    (ALICE, 10),            # self-transfer
    ("254799999999", 10),   # unknown receiver
])
def test_transfer_rejected_without_side_effects(receiver, amount):
    assert not database.transfer(ALICE, receiver, amount)
    assert database.get_balance(ALICE) == 1500
    assert database.get_balance(BOB) == 500
    assert database.mini_statement(ALICE) == []


def test_lockout_after_three_wrong_pins():
    for _ in range(3):
        assert not database.check_pin(ALICE, "0000")
    assert database.is_locked(ALICE)
    assert not database.check_pin(ALICE, PIN)  # correct PIN no longer works


def test_correct_pin_resets_failure_counter():
    database.check_pin(ALICE, "0000")
    database.check_pin(ALICE, "0000")
    assert database.check_pin(ALICE, PIN)
    assert database.attempts_left(ALICE) == 3


# ---------------------------------------------------------------- USSD flows

def test_main_menu(client):
    reply = ussd(client, "")
    assert reply.startswith("CON Welcome to PyWallet")
    assert "4. Mini Statement" in reply


def test_send_money_full_flow(client):
    assert ussd(client, "1") == "CON Enter recipient phone number:"
    assert ussd(client, f"1*{BOB}") == "CON Enter amount:"
    assert "Enter PIN to confirm" in ussd(client, f"1*{BOB}*250")
    reply = ussd(client, f"1*{BOB}*250*{PIN}")
    assert reply.startswith("END Sent KES 250.00")
    assert "KES 1250.00" in reply
    assert database.get_balance(BOB) == 750


def test_send_money_accepts_local_number_format(client):
    reply = ussd(client, f"1*0700000002*100*{PIN}")
    assert reply.startswith("END Sent KES 100.00 to 254700000002")


def test_send_money_to_self_rejected(client):
    assert ussd(client, f"1*{ALICE}") == "END You cannot send money to yourself."


@pytest.mark.parametrize("amount", ["abc", "nan", "inf", "-5", "0", "1e3", "10.555", "999999"])
def test_invalid_amounts_rejected(client, amount):
    assert ussd(client, f"1*{BOB}*{amount}") == "END Invalid amount. Session ended."
    assert ussd(client, f"3*{amount}") == "END Invalid amount. Session ended."


def test_insufficient_balance(client):
    assert ussd(client, f"1*{BOB}*2000") == "END Insufficient balance. Session ended."


def test_wrong_pin_reports_attempts_then_locks(client):
    assert ussd(client, "2*0000") == "END Wrong PIN. 2 attempt(s) left."
    assert ussd(client, "2*0000") == "END Wrong PIN. 1 attempt(s) left."
    assert ussd(client, "2*0000") == "END Wrong PIN. Your account is now locked."
    assert ussd(client, "").startswith("END Your account is locked")


def test_check_balance(client):
    assert ussd(client, "2") == "CON Enter PIN:"
    assert ussd(client, f"2*{PIN}") == "END Your balance is KES 1500.00"


def test_buy_airtime_requires_pin(client):
    assert "Enter PIN to confirm" in ussd(client, "3*50")
    assert ussd(client, "3*50*0000").startswith("END Wrong PIN")
    assert database.get_balance(ALICE) == 1500
    assert ussd(client, f"3*50*{PIN}").startswith("END Bought KES 50.00 airtime")
    assert database.get_balance(ALICE) == 1450


def test_mini_statement(client):
    assert ussd(client, f"4*{PIN}") == "END No transactions yet."
    ussd(client, f"1*{BOB}*100*{PIN}")
    ussd(client, f"3*20*{PIN}")
    reply = ussd(client, f"4*{PIN}")
    lines = reply.splitlines()
    assert lines[0] == "END Last transactions:"
    assert "Airtime -20.00" in lines[1]          # newest first
    assert f"To {BOB} -100.00" in lines[2]
    assert f"From {ALICE} +100.00" in ussd(client, f"4*{PIN}", phone=BOB)


def test_change_pin(client):
    assert ussd(client, "5*0000").startswith("END Wrong PIN")
    assert ussd(client, f"5*{PIN}") == "CON Enter new 4-digit PIN:"
    assert ussd(client, f"5*{PIN}*12") == "END PIN must be exactly 4 digits. Session ended."
    assert ussd(client, f"5*{PIN}*5678*5679") == "END PINs do not match. Session ended."
    assert ussd(client, f"5*{PIN}*5678*5678") == "END PIN changed successfully."
    assert database.check_pin(ALICE, "5678")
    assert not database.check_pin(ALICE, PIN)


def test_registration_flow(client):
    new = "254711223344"
    assert ussd(client, "", phone=new).startswith("CON Welcome to PyWallet.\nThis number is not registered")
    assert ussd(client, "1", phone=new) == "CON Create a 4-digit PIN:"
    assert ussd(client, "1*99", phone=new) == "END PIN must be exactly 4 digits. Session ended."
    assert ussd(client, "1*4321*4322", phone=new) == "END PINs do not match. Session ended."
    assert ussd(client, "1*4321*4321", phone=new).startswith("END Registration successful")
    assert database.check_pin(new, "4321")
    assert ussd(client, "", phone=new).startswith("CON Welcome to PyWallet\n1. Send Money")


def test_exit_and_invalid_option(client):
    assert ussd(client, "0") == "END Thank you for using PyWallet."
    assert ussd(client, "9") == "END Invalid option. Session ended."


def test_index_page_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "USSD Wallet Simulator" in r.text
