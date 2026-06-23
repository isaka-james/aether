"""Aether FastAPI application: auth, text/voice command endpoints, live
notifications over WebSocket, and the static web client.
"""
import asyncio
import logging
import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (Depends, FastAPI, File, HTTPException, UploadFile,
                     WebSocket, WebSocketDisconnect, status)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import cache, db, llm, notifications, orchestrator, stt
from .auth import create_token, decode_token, require_user, verify_credentials
from .config import get_settings
from .models import (ApproveCommand, CommandResult, LoginRequest, TextCommand,
                     TokenResponse)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("aether")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
# Serve the PWA manifest with the correct type so browsers offer to install the app.
mimetypes.add_type("application/manifest+json", ".webmanifest")


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
    result = await orchestrator.handle(body.text, transcript=body.text, clarify=body.clarify,
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
        # Silent on the host, detailed on the web — the user can read what went wrong and
        # try again. STT errors aren't something the speakers should narrate.
        result = await orchestrator.speak(
            "I didn't catch any audio that time. Do try again.",
            status="error", ok=False,
            detail=f"Transcription failed: {type(e).__name__}: {e}. Check the microphone "
                   "permission and that the recording carried real audio.",
            on_progress=_progress)
        await _notify(result)
        return result

    if not transcript:
        result = await orchestrator.speak(
            "I didn't catch any speech in that recording. Try once more.",
            status="error", ok=False,
            detail="The transcriber returned an empty string — either the audio was silent, "
                   "below the VAD threshold, or both online and local recognisers came back blank.",
            on_progress=_progress)
        await _notify(result)
        return result

    result = await orchestrator.handle(transcript, transcript=transcript,
                                       session=user, on_progress=_progress)
    await _notify(result)
    return result


# Shown as suggestion chips before any history exists (or if the DB is off).
_DEFAULT_SUGGESTIONS = [
    "play lofi hip hop on youtube", "what's the weather", "lock the screen",
    "make the video full screen", "how many windows are open", "how much RAM is free",
]


@app.get("/api/suggestions")
async def suggestions(user: str = Depends(require_user)):
    """Quick chips for the home page: the user's most-RECENT requests first, then their
    most-asked, then sensible defaults — de-duplicated and capped. Recency leads so the home
    reflects what they just did. `from_history` lets the client label the row ("Recent" vs a
    generic prompt) and is False when there's no real history (or persistence is off)."""
    recent = await db.recent_requests(limit=6, session=user)
    frequent = await db.top_requests(limit=6, session=user)
    items: list[str] = []
    seen: set[str] = set()
    for source in (recent, frequent, _DEFAULT_SUGGESTIONS):
        for c in source:
            key = (c or "").strip().lower()
            if key and key not in seen:
                items.append(c.strip())
                seen.add(key)
    return {"suggestions": items[:6], "from_history": bool(recent or frequent)}


@app.get("/api/health")
async def health():
    s = get_settings()
    return {"ok": True, "llm": llm.provider_info(), "voice": s.kokoro_voice,
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


@app.get("/sw.js")
async def service_worker():
    # Served from the root so the service worker can control the whole app (PWA install).
    return FileResponse(WEB_DIR / "sw.js", media_type="application/javascript")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
