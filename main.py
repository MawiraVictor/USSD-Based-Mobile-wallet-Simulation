from fastapi import FastAPI, Form, Request
from fastapi.responses import PlainTextResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from database import init_db
from state_machine import handle

app = FastAPI()
init_db()

# session store: sessionId -> {"phone": ..., "last_text": ...}
sessions: dict = {}

@app.post("/ussd", response_class=PlainTextResponse)
async def ussd(
    sessionId: str = Form(...),
    phoneNumber: str = Form(...),
    text: str = Form(""),
):
    session = sessions.setdefault(sessionId, {"phone": phoneNumber})
    response = handle(phoneNumber, text, session)

    if response.startswith("END"):
        sessions.pop(sessionId, None)
    return response

@app.get("/", response_class=HTMLResponse)
async def index():
    with open("static/index.html") as f:
        return f.read()