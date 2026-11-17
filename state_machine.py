"""USSD menu logic.

USSD gateways send the *cumulative* input for a session, joined by '*'.
For example, a user who picks "1", types a number, then an amount sends:
    ""  ->  "1"  ->  "1*254700000002"  ->  "1*254700000002*100"
So the depth of the menu is simply the number of '*'-separated parts.

Every response starts with "CON " (keep the session open and wait for input)
or "END " (show the message and close the session).
"""
import re

from database import (
    attempts_left,
    buy_airtime,
    change_pin,
    check_pin,
    get_balance,
    is_locked,
    mini_statement,
    register_user,
    transfer,
    user_exists,
)

MAIN_MENU = (
    "CON Welcome to PyWallet\n"
    "1. Send Money\n"
    "2. Check Balance\n"
    "3. Buy Airtime\n"
    "4. Mini Statement\n"
    "5. Change PIN\n"
    "0. Exit"
)

REGISTER_MENU = (
    "CON Welcome to PyWallet.\n"
    "This number is not registered.\n"
    "1. Register\n"
    "0. Exit"
)

MAX_AMOUNT = 150_000

_AMOUNT_RE = re.compile(r"^\d+(\.\d{1,2})?$")
_PIN_RE = re.compile(r"^\d{4}$")


def parse_amount(raw: str) -> float | None:
    """Return a positive amount with at most 2 decimals, or None if invalid."""
    if not _AMOUNT_RE.match(raw):
        return None
    amount = float(raw)
    if amount <= 0 or amount > MAX_AMOUNT:
        return None
    return amount


def normalize_phone(raw: str) -> str:
    """Accept 07XXXXXXXX / 01XXXXXXXX / +2547XXXXXXXX and return 2547XXXXXXXX."""
    raw = raw.strip().lstrip("+")
    if len(raw) == 10 and raw.startswith("0"):
        return "254" + raw[1:]
    return raw


def wrong_pin(phone: str) -> str:
    left = attempts_left(phone)
    if left == 0:
        return "END Wrong PIN. Your account is now locked."
    return f"END Wrong PIN. {left} attempt(s) left."


def handle(phone: str, text: str, session: dict) -> str:
    """
    phone   = caller's number (from the gateway)
    text    = cumulative input, e.g. "" or "1" or "1*254700000002"
    session = dict that persists per sessionId (unused for now; reserved)
    """
    inputs = text.split("*") if text else []

    if not user_exists(phone):
        return _handle_registration(phone, inputs)

    if is_locked(phone):
        return "END Your account is locked after too many wrong PIN attempts."

    if not inputs:
        return MAIN_MENU

    choice, rest = inputs[0], inputs[1:]
    handler = {
        "1": _send_money,
        "2": _check_balance,
        "3": _buy_airtime,
        "4": _mini_statement,
        "5": _change_pin,
    }.get(choice)

    if choice == "0":
        return "END Thank you for using PyWallet."
    if handler is None:
        return "END Invalid option. Session ended."
    return handler(phone, rest)


# ---------------------------------------------------------------- menus

def _handle_registration(phone: str, inputs: list[str]) -> str:
    if not inputs:
        return REGISTER_MENU
    if inputs[0] != "1":
        return "END Thank you for using PyWallet."
    if len(inputs) == 1:
        return "CON Create a 4-digit PIN:"
    if not _PIN_RE.match(inputs[1]):
        return "END PIN must be exactly 4 digits. Session ended."
    if len(inputs) == 2:
        return "CON Confirm your PIN:"
    if inputs[2] != inputs[1]:
        return "END PINs do not match. Session ended."
    if not register_user(phone, inputs[1]):
        return "END Registration failed. Session ended."
    return "END Registration successful. Dial again to use PyWallet."


def _send_money(phone: str, rest: list[str]) -> str:
    if len(rest) == 0:
        return "CON Enter recipient phone number:"

    recipient = normalize_phone(rest[0])
    if recipient == phone:
        return "END You cannot send money to yourself."
    if not user_exists(recipient):
        return "END Recipient not found. Session ended."
    if len(rest) == 1:
        return "CON Enter amount:"

    amount = parse_amount(rest[1])
    if amount is None:
        return "END Invalid amount. Session ended."
    if amount > get_balance(phone):
        return "END Insufficient balance. Session ended."
    if len(rest) == 2:
        return f"CON Send KES {amount:.2f} to {recipient}?\nEnter PIN to confirm:"

    if not check_pin(phone, rest[2]):
        return wrong_pin(phone)
    if not transfer(phone, recipient, amount):
        return "END Transaction failed. Session ended."
    return (
        f"END Sent KES {amount:.2f} to {recipient}.\n"
        f"New balance: KES {get_balance(phone):.2f}"
    )


def _check_balance(phone: str, rest: list[str]) -> str:
    if len(rest) == 0:
        return "CON Enter PIN:"
    if not check_pin(phone, rest[0]):
        return wrong_pin(phone)
    return f"END Your balance is KES {get_balance(phone):.2f}"


def _buy_airtime(phone: str, rest: list[str]) -> str:
    if len(rest) == 0:
        return "CON Enter airtime amount:"

    amount = parse_amount(rest[0])
    if amount is None:
        return "END Invalid amount. Session ended."
    if amount > get_balance(phone):
        return "END Insufficient balance. Session ended."
    if len(rest) == 1:
        return f"CON Buy KES {amount:.2f} airtime?\nEnter PIN to confirm:"

    if not check_pin(phone, rest[1]):
        return wrong_pin(phone)
    if not buy_airtime(phone, amount):
        return "END Transaction failed. Session ended."
    return (
        f"END Bought KES {amount:.2f} airtime.\n"
        f"New balance: KES {get_balance(phone):.2f}"
    )


def _mini_statement(phone: str, rest: list[str]) -> str:
    if len(rest) == 0:
        return "CON Enter PIN:"
    if not check_pin(phone, rest[0]):
        return wrong_pin(phone)

    rows = mini_statement(phone)
    if not rows:
        return "END No transactions yet."
    lines = ["END Last transactions:"]
    for r in rows:
        date = str(r["created_at"])[:10]
        if r["type"] == "airtime":
            lines.append(f"{date} Airtime -{r['amount']:.2f}")
        elif r["sender"] == phone:
            lines.append(f"{date} To {r['receiver']} -{r['amount']:.2f}")
        else:
            lines.append(f"{date} From {r['sender']} +{r['amount']:.2f}")
    return "\n".join(lines)


def _change_pin(phone: str, rest: list[str]) -> str:
    if len(rest) == 0:
        return "CON Enter current PIN:"
    if len(rest) == 1:
        # Validate the current PIN before asking for a new one.
        if not check_pin(phone, rest[0]):
            return wrong_pin(phone)
        return "CON Enter new 4-digit PIN:"
    if not _PIN_RE.match(rest[1]):
        return "END PIN must be exactly 4 digits. Session ended."
    if len(rest) == 2:
        return "CON Confirm new PIN:"
    if rest[2] != rest[1]:
        return "END PINs do not match. Session ended."
    # Re-check the current PIN: the gateway resends the whole string, and a
    # client could skip straight to this step without passing the check above.
    if not check_pin(phone, rest[0]):
        return wrong_pin(phone)
    change_pin(phone, rest[1])
    return "END PIN changed successfully."
