"""Aether FastAPI application: auth, text/voice command endpoints, live
notifications over WebSocket, and the static web client.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (Depends, FastAPI, File, Form, HTTPException, UploadFile,
                     WebSocket, WebSocketDisconnect, status)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import cache, db, notifications, orchestrator, stt
from .auth import create_token, decode_token, require_user, verify_credentials
from .config import get_settings
from .models import (ApproveCommand, CommandResult, LoginRequest, TextCommand,
                     TokenResponse)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("aether")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Bring up the optional persistence layer; both degrade gracefully if unreachable.
    await db.connect()
    await cache.connect()
    tasks = [
        asyncio.create_task(notifications.poll_loop(hub.broadcast)),
        asyncio.create_task(notifications.subscriber(hub.broadcast)),
    ]
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await cache.close()
        await db.close()


app = FastAPI(title="Aether", version="1.0.0", lifespan=lifespan)


class Hub:
    """Broadcasts 'task done' notifications to every connected web client."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def join(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.add(ws)

    async def leave(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, message: dict) -> None:
        async with self._lock:
            dead = []
            for ws in self._clients:
                try:
                    await ws.send_json(message)
                except Exception:  # noqa: BLE001
                    dead.append(ws)
            for ws in dead:
                self._clients.discard(ws)


hub = Hub()


async def _notify(result: CommandResult) -> None:
    await hub.broadcast({
        "type": "task_done",
        "status": result.status,
        "skill": result.skill,
        "summary": result.summary,
        "transcript": result.transcript,
    })


async def _progress(step: str, label: str) -> None:
    """Push a 'which step are we on' update to all connected web clients."""
    await hub.broadcast({"type": "progress", "step": step, "label": label})


# --- Auth ---------------------------------------------------------------------
@app.post("/api/login", response_model=TokenResponse)
async def login(body: LoginRequest):
    if not verify_credentials(body.username, body.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    return TokenResponse(access_token=create_token(body.username))


# --- Commands -----------------------------------------------------------------
@app.post("/api/command/text", response_model=CommandResult)
async def command_text(body: TextCommand, user: str = Depends(require_user)):
    await _progress("received", "Received by Aether")
    result = await orchestrator.handle(body.text, transcript=body.text,
                                       confirmed=body.confirmed, clarify=body.clarify,
                                       session=user, on_progress=_progress)
    await _notify(result)
    return result


@app.post("/api/command/approve", response_model=CommandResult)
async def command_approve(body: ApproveCommand, user: str = Depends(require_user)):
    """Run an action the user approved in the UI (e.g. a risky or root command),
    without re-running the LLM decision pass."""
    await _progress("received", "Approval received by Aether")
    result = await orchestrator.execute_approved(
        body.skill, body.params, transcript=body.transcript, on_progress=_progress)
    await _notify(result)
    return result


@app.post("/api/command/voice", response_model=CommandResult)
async def command_voice(
    audio: UploadFile = File(...),
    confirmed: bool = Form(False),
    user: str = Depends(require_user),
):
    raw = await audio.read()
    suffix = Path(audio.filename or "audio.webm").suffix or ".webm"
    await _progress("received", "Received by Aether")
    await _progress("transcribing", "Transcribing your voice…")
    try:
        transcript = await asyncio.to_thread(stt.transcribe, raw, suffix)
    except Exception as e:  # noqa: BLE001
        log.exception("transcription failed")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Transcription failed: {e}")

    if not transcript:
        return CommandResult(ok=False, status="error", summary="I couldn't make out any speech.")

    result = await orchestrator.handle(transcript, transcript=transcript,
                                       confirmed=confirmed, session=user, on_progress=_progress)
    await _notify(result)
    return result


@app.get("/api/health")
async def health():
    s = get_settings()
    return {"ok": True, "provider": "deepseek", "model": s.deepseek_model,
            "llm_configured": bool(s.deepseek_api_key), "voice": s.kokoro_voice,
            "whisper": s.whisper_model, "database": db.enabled(), "redis": cache.enabled()}


# --- Live notifications -------------------------------------------------------
@app.websocket("/ws")
async def ws_notifications(ws: WebSocket, token: str = ""):
    if not token or decode_token(token) is None:
        await ws.close(code=4401)
        return
    await ws.accept()
    await hub.join(ws)
    try:
        while True:
            await ws.receive_text()  # keepalive / ignored
    except WebSocketDisconnect:
        pass
    finally:
        await hub.leave(ws)


# --- Static web client --------------------------------------------------------
@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
