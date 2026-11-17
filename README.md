# USSD-Based Mobile Wallet Simulation

A small mobile-money wallet (in the spirit of M-Pesa style `*141#` menus) built with
**FastAPI** and **SQLite**. It exposes a USSD gateway–compatible endpoint and ships with a
browser-based phone simulator so you can try every menu without a real gateway.

## Features

- **Send Money**, with a confirmation step and recipient validation (accepts `07…` or `2547…`)
- **Check Balance**
- **Buy Airtime**
- **Mini Statement**: the last 5 transactions
- **Change PIN**
- **Registration** for numbers that aren't in the system yet
- PINs stored as salted PBKDF2 hashes; account locks after 3 wrong attempts
- Every money movement is a single atomic SQLite transaction

## Quick start

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Open <http://127.0.0.1:8000>, press **DIAL**, and use the keypad (or your keyboard).

Demo accounts (created automatically on first run):

| Phone          | Balance   | PIN    |
|----------------|-----------|--------|
| `254700000001` | KES 1,500 | `1234` |
| `254700000002` | KES 500   | `1234` |

Type any other number into the *Your number* field to go through registration.

## How USSD sessions work

A USSD gateway (e.g. Africa's Talking) POSTs form data to `/ussd` on every keypress-submit:

| Field         | Example          |
|---------------|------------------|
| `sessionId`   | `ATUid_abc123`   |
| `phoneNumber` | `+254700000001`  |
| `serviceCode` | `*141#`          |
| `text`        | `1*254700000002*100` |

`text` is **cumulative**: every answer so far, joined by `*`. The state machine
(`state_machine.py`) splits it and uses the number of parts as the menu depth. Responses
start with `CON` (show the text and wait for input) or `END` (show the text and close the session).

Try it with curl:

```bash
curl -X POST http://127.0.0.1:8000/ussd -d sessionId=1 -d phoneNumber=254700000001 -d text=""
curl -X POST http://127.0.0.1:8000/ussd -d sessionId=1 -d phoneNumber=254700000001 -d text="2*1234"
```

## Menu map

```
*141#
├─ 1 Send Money   → recipient → amount → confirm with PIN
├─ 2 Check Balance → PIN
├─ 3 Buy Airtime  → amount → confirm with PIN
├─ 4 Mini Statement → PIN
├─ 5 Change PIN   → current PIN → new PIN → confirm
└─ 0 Exit
```

## Project layout

```
main.py            FastAPI app: /ussd endpoint and simulator page
state_machine.py   USSD menu logic (pure function of phone + cumulative text)
database.py        SQLite schema, PIN hashing, atomic transfers
static/index.html  Browser phone simulator
tests/             pytest suite
```

## Running tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

Tests use a temporary database. Set `WALLET_DB=/path/to/file.db` to point the app at a
different database.

## License

MIT. See [LICENSE](LICENSE).
