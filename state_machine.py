from database import get_balance, user_exists, check_pin, transfer

MAIN_MENU = (
    "CON Welcome to PyWallet\n"
    "1. Send Money\n"
    "2. Check Balance\n"
    "3. Buy Airtime\n"
    "4. Exit"
)

def handle(phone: str, text: str, session: dict) -> str:
    """
    text = cumulative input, e.g. "" or "1" or "1*254700000002"
    session = dict that persists per sessionId
    Returns a string starting with CON (continue) or END (terminate).
    """
    inputs = text.split("*") if text else []

    # No input yet → main menu
    if len(inputs) == 0:
        return MAIN_MENU

    choice = inputs[0]

    # ---- 1. Send Money ----
    if choice == "1":
        if len(inputs) == 1:
            return "CON Enter recipient phone number:"
        if len(inputs) == 2:
            if not user_exists(inputs[1]):
                return "END Recipient not found. Session ended."
            return "CON Enter amount:"
        if len(inputs) == 3:
            try:
                amt = float(inputs[2])
                if amt <= 0:
                    raise ValueError
            except ValueError:
                return "END Invalid amount. Session ended."
            if amt > get_balance(phone):
                return "END Insufficient balance. Session ended."
            return "CON Enter PIN to confirm:"
        if len(inputs) == 4:
            recipient, amt, pin = inputs[1], float(inputs[2]), inputs[3]
            if not check_pin(phone, pin):
                return "END Wrong PIN. Session ended."
            ok = transfer(phone, recipient, amt)
            if ok:
                return f"END Sent KES {amt:.2f} to {recipient}. New balance: KES {get_balance(phone):.2f}"
            return "END Transaction failed. Session ended."

    # ---- 2. Check Balance ----
    if choice == "2":
        if len(inputs) == 1:
            return "CON Enter PIN:"
        if len(inputs) == 2:
            if not check_pin(phone, inputs[1]):
                return "END Wrong PIN. Session ended."
            return f"END Your balance is KES {get_balance(phone):.2f}"

    # ---- 3. Buy Airtime ----
    if choice == "3":
        if len(inputs) == 1:
            return "CON Enter airtime amount:"
        if len(inputs) == 2:
            try:
                amt = float(inputs[1])
                if amt <= 0:
                    raise ValueError
            except ValueError:
                return "END Invalid amount. Session ended."
            if amt > get_balance(phone):
                return "END Insufficient balance. Session ended."
            # Simple simulation: debit self, log as airtime
            from database import get_conn
            with get_conn() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("UPDATE users SET balance = balance - ? WHERE phone=?", (amt, phone))
                conn.execute(
                    "INSERT INTO transactions (sender, receiver, amount, type, status) VALUES (?,?,?,?,?)",
                    (phone, "AIRTIME", amt, "airtime", "success")
                )
                conn.execute("COMMIT")
            return f"END Bought KES {amt:.2f} airtime. New balance: KES {get_balance(phone):.2f}"

    # ---- 4. Exit ----
    if choice == "4":
        return "END Thank you for using PyWallet."

    return "END Invalid option. Session ended."