import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from src.invite import validate_invite
from src.session import run_session

app = FastAPI(title="voice-assist")

STATIC_DIR = Path(__file__).parent.parent / "static"


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, invite: str = ""):
    await websocket.accept()

    if not invite:
        await websocket.send_json({"type": "error", "code": 4001, "message": "No invite token provided."})
        await websocket.close(code=4001)
        return

    valid = await validate_invite(invite)
    if not valid:
        await websocket.send_json({"type": "error", "code": 4001, "message": "This invite link has expired or is invalid."})
        await websocket.close(code=4001)
        return

    logger.info("New session for invite %s...", invite[:8])
    try:
        await run_session(websocket)
    except WebSocketDisconnect:
        logger.info("Client disconnected (invite %s...)", invite[:8])
    except Exception as e:
        logger.error("Session error: %s", e)


if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
