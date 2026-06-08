import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from src import calendar_service
from src.config import GCP_LOCATION, GCP_PROJECT_ID, GEMINI_MODEL
from src.invite import validate_invite
from src.observability import TraceLogger
from src.session import run_session

app = FastAPI(title="voice-assist")

STATIC_DIR = Path(__file__).parent.parent / "static"


@app.get("/health")
async def health():
    return {"status": "ok"}


def _voice_readiness() -> dict:
    if not GCP_PROJECT_ID or not GCP_LOCATION or not GEMINI_MODEL:
        return {"ready": False, "message": "Voice configuration is incomplete."}
    try:
        from pipecat.services.google.gemini_live.vertex.llm import GeminiLiveVertexLLMService

        GeminiLiveVertexLLMService(
            project_id=GCP_PROJECT_ID,
            location=GCP_LOCATION,
            voice_id="Leda",
            system_instruction="Readiness check.",
            settings=GeminiLiveVertexLLMService.Settings(
                model=GEMINI_MODEL.replace("google/", ""),
            ),
        )
        return {"ready": True, "message": "Voice ready."}
    except Exception as exc:
        return {"ready": False, "message": f"Voice service unavailable: {exc}"}


@app.get("/readiness")
async def readiness():
    voice = _voice_readiness()
    calendar = await calendar_service.readiness_check()
    return {
        "ready": voice["ready"] and calendar["ready"],
        "voice": voice,
        "calendar": calendar,
    }


@app.get("/debug/sessions/{session_id}")
async def debug_session(session_id: str):
    if os.environ.get("TRACE_DEBUG_ENDPOINT", "1") == "0":
        raise HTTPException(status_code=404)
    if not session_id.replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid session id.")
    trace_dir = Path(os.environ.get("TRACE_LOCAL_DIR", ".traces"))
    path = trace_dir / f"{session_id}.jsonl"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Trace not found locally.")
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            import json

            records.append(json.loads(line))
    return {"session_id": session_id, "records": records}


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

    trace = TraceLogger.for_invite(invite)
    await trace.start_session({
        "model": GEMINI_MODEL,
        "region": GCP_LOCATION,
        "client": {
            "user_agent": websocket.headers.get("user-agent", ""),
            "host": websocket.headers.get("host", ""),
        },
    })
    await websocket.send_json({"type": "trace_session", "session_id": trace.session_id})

    logger.info("New session for invite %s...", invite[:8])
    session_status = "completed"
    try:
        await run_session(websocket, trace=trace)
    except WebSocketDisconnect:
        logger.info("Client disconnected (invite %s...)", invite[:8])
        session_status = "disconnected"
    except Exception as e:
        logger.error("Session error: %s", e)
        session_status = "error"
        await trace.event("session_error", {"error": str(e)})
    finally:
        await trace.end_session(session_status)


if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
