from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form
from fastapi.responses import FileResponse, PlainTextResponse

from database import init_db
from state_machine import handle

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="PyWallet USSD Simulator", lifespan=lifespan)

# session store: sessionId -> {"phone": ...}
sessions: dict = {}


@app.post("/ussd", response_class=PlainTextResponse)
async def ussd(
    sessionId: str = Form(...),
    phoneNumber: str = Form(...),
    text: str = Form(""),
    serviceCode: str = Form("*141#"),  # sent by real gateways; unused here
):
    phone = phoneNumber.strip().lstrip("+")
    session = sessions.setdefault(sessionId, {"phone": phone})
    response = handle(phone, text, session)

    if response.startswith("END"):
        sessions.pop(sessionId, None)
    return response


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")
