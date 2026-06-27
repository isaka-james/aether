"""Play WAV audio on the host speakers. Stdlib only.

Playback is SERIALIZED through a single background worker: every clip is enqueued and played
strictly one-at-a-time, in order. This is what makes sentence-by-sentence streaming sound right
(no two clips ever overlap) and is also why a reply can never be spoken twice at once.

A `flush` clears anything still queued and stops whatever is playing — the backend sets it on the
first clip of a new reply, so a fresh answer instantly supersedes a stale one (e.g. audio that
backed up while the screen was locked, instead of all of it blurting out at once on unlock).

Tries PipeWire/PulseAudio first (paplay), then ffplay, then aplay — whichever is present.
"""
import logging
import os
import queue
import shutil
import subprocess
import tempfile
import threading

log = logging.getLogger("aether-agent.audio")

# A clip not started within this long is treated as stale and skipped — bounds how far behind a
# blocked sink (e.g. suspended while locked) can get before it stops spilling old audio on resume.
_MAX_PLAY_SECONDS = 30
_MAX_QUEUE = 64  # hard cap so a wedged player can't let the backlog grow without bound

# (epoch, wav_bytes) items; the worker skips any whose epoch is behind the current one (flushed).
_q: "queue.Queue[tuple[int, bytes]]" = queue.Queue()
_epoch = 0
_epoch_lock = threading.Lock()
_current: "subprocess.Popen | None" = None
_current_lock = threading.Lock()
_worker_started = False
_worker_lock = threading.Lock()


def _player_argv(path: str) -> list[str] | None:
    for base in (["paplay"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"], ["aplay", "-q"]):
        if shutil.which(base[0]):
            return base + [path]
    return None


def _play_blocking(wav_bytes: bytes) -> bool:
    """Play one clip to completion. Tracks the process so a flush can interrupt it. Never raises."""
    global _current
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(wav_bytes)
        path = f.name
    try:
        argv = _player_argv(path)
        if argv is None:
            return False
        try:
            p = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:  # noqa: BLE001
            log.warning("failed to start player %s: %s", argv[0], e)
            return False
        with _current_lock:
            _current = p
        try:
            return p.wait(timeout=_MAX_PLAY_SECONDS) == 0
        except subprocess.TimeoutExpired:
            log.warning("playback exceeded %ds; killing it.", _MAX_PLAY_SECONDS)
            p.kill()
            return False
        finally:
            with _current_lock:
                _current = None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _worker() -> None:
    while True:
        epoch, wav = _q.get()
        try:
            with _epoch_lock:
                current = _epoch
            if epoch == current and wav:   # skip clips a later flush superseded
                _play_blocking(wav)
        except Exception:  # noqa: BLE001 - one bad clip must never kill the worker
            log.exception("playback worker hit an error; continuing")
        finally:
            _q.task_done()


def _ensure_worker() -> None:
    global _worker_started
    if _worker_started:
        return
    with _worker_lock:
        if not _worker_started:
            threading.Thread(target=_worker, name="audio-player", daemon=True).start()
            _worker_started = True


def _flush() -> None:
    """Supersede anything queued or playing: bump the epoch, drain the queue, stop the current clip."""
    global _epoch
    with _epoch_lock:
        _epoch += 1
    while True:
        try:
            _q.get_nowait()
            _q.task_done()
        except queue.Empty:
            break
    with _current_lock:
        if _current is not None and _current.poll() is None:
            try:
                _current.terminate()
            except Exception:  # noqa: BLE001
                pass


def play_wav(wav_bytes: bytes, flush: bool = False) -> bool:
    """Enqueue a clip for the serialized player. `flush` first cancels any pending/playing audio
    (used on the first clip of a new reply). Returns True once accepted — playback then happens in
    order on the worker. Drops the clip (returns False) only if there's no audio or the queue is
    saturated by a wedged player."""
    if not wav_bytes:
        return False
    _ensure_worker()
    if flush:
        _flush()
    if _q.qsize() >= _MAX_QUEUE:
        log.warning("audio queue saturated (%d); dropping clip.", _q.qsize())
        return False
    with _epoch_lock:
        epoch = _epoch
    _q.put((epoch, wav_bytes))
    return True
