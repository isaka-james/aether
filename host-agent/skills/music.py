"""
Local music skills: browse and play the user's own music under ~/Music.

The agent navigates the library like a folder tree: ``list_music`` with no args shows the
top level of ~/Music (albums/folders + any loose tracks); pass ``path`` to step into a
folder, or ``query`` to search the whole tree by name. It then plays with ``play_music``,
giving exact track paths or a folder (whole album). Playback uses a visible command-line
player (VLC window preferred, then mpv) so the user can see and control it; ``stop_playback`` ends it.
"""
from __future__ import annotations

import difflib
import os
import re
import signal
import subprocess

from config import MUSIC_DIR
from ._util import fail, has, ok
from .registry import skill

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".m4b", ".ogg", ".opus", ".wav", ".aac", ".wma"}
_PIDFILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "music.pid")
_MAX_LISTED = 40  # keep observations small enough for the model to read
_BASE = lambda: os.path.realpath(MUSIC_DIR)

# Filler words to strip from a spoken request so the search keys on real terms (artist,
# album, song) instead of the whole sentence — which a literal match would never find.
_STOP = {"play", "plays", "playing", "played", "some", "any", "song", "songs", "music",
         "track", "tracks", "album", "albums", "the", "a", "an", "please", "put", "on",
         "for", "me", "by", "listen", "to", "from", "of", "and", "or", "my", "i", "want",
         "wanna", "gimme", "give", "stuff", "something", "sum", "need", "their", "his",
         "her", "that", "this", "with", "feat"}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _terms(query: str) -> list[str]:
    """Significant search terms from a (possibly imperfect speech-to-text) request."""
    return [w for w in _words(query) if len(w) >= 2 and w not in _STOP]


def _safe(rel: str) -> str | None:
    """Resolve a path relative to MUSIC_DIR, refusing anything outside it."""
    base = _BASE()
    p = os.path.realpath(rel if os.path.isabs(rel) else os.path.join(MUSIC_DIR, rel))
    return p if (p == base or p.startswith(base + os.sep)) else None


def _is_audio(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in AUDIO_EXTS


def _all_tracks() -> list[str]:
    """Every audio file under MUSIC_DIR, as sorted relative paths."""
    out: list[str] = []
    for root, _dirs, files in os.walk(MUSIC_DIR):
        out += [os.path.relpath(os.path.join(root, fn), MUSIC_DIR) for fn in files if _is_audio(fn)]
    return sorted(out)


def _match(terms: list[str], path_lower: str, path_words: list[str]) -> int:
    """How well a track's path matches the search terms. 2 = exact term in path,
    1 = a close (fuzzy) word — tolerant of small speech-to-text errors. Sums per term."""
    score = 0
    for t in terms:
        if t in path_lower:
            score += 2
        elif difflib.get_close_matches(t, path_words, n=1, cutoff=0.8):
            score += 1
    return score


def _search(query: str) -> list[tuple[str, int]]:
    """Tracks ranked by relevance to `query`. With no real terms, returns all (score 0).
    Otherwise returns only tracks matching at least one term, best first."""
    terms = _terms(query)
    ranked: list[tuple[str, int]] = []
    for rel in _all_tracks():
        if not terms:
            ranked.append((rel, 0))
            continue
        score = _match(terms, rel.lower(), _words(rel))
        if score > 0:
            ranked.append((rel, score))
    ranked.sort(key=lambda rs: (-rs[1], rs[0]))
    return ranked


def _list_dir(rel: str):
    """(folders, tracks) as paths relative to MUSIC_DIR for one directory, or None if bad."""
    target = _safe(rel) if rel else _BASE()
    if not target or not os.path.isdir(target):
        return None
    folders, tracks = [], []
    for e in sorted(os.scandir(target), key=lambda x: x.name.lower()):
        if e.name.startswith("."):
            continue
        child = f"{rel}/{e.name}" if rel else e.name
        if e.is_dir():
            folders.append(child)
        elif _is_audio(e.name):
            tracks.append(child)
    return folders, tracks


def _resolve(params: dict) -> list[str]:
    """Absolute, sandboxed file list from path(s)/folder(s), a query, or the whole library."""
    raw = params.get("paths") or params.get("path") or []
    if isinstance(raw, str):
        raw = [raw]
    files: list[str] = []
    if raw:
        for r in raw:
            rp = _safe(r)
            if not rp:
                continue
            if os.path.isdir(rp):  # a folder/album -> all audio inside it
                for root, _d, fs in os.walk(rp):
                    files += [os.path.join(root, fn) for fn in sorted(fs) if _is_audio(fn)]
            elif os.path.isfile(rp):
                files.append(rp)
    else:
        q = str(params.get("query", "")).strip()
        ranked = _search(q)
        if not q:
            files = [os.path.join(MUSIC_DIR, rel) for rel, _ in ranked]   # everything
        elif ranked:
            top = ranked[0][1]  # play the most-relevant cluster (best-scoring tracks)
            files = [os.path.join(MUSIC_DIR, rel) for rel, sc in ranked if sc == top]
    return files


def _player_argv(files: list[str], shuffle: bool) -> list[str] | None:
    """A command-line player that opens a VISIBLE window the user can see/control.
    Prefer VLC for reliable window visibility on Linux, then mpv, then ffplay as fallback."""
    multi = len(files) > 1
    if has("vlc"):  # the GUI VLC, not headless cvlc
        argv = ["vlc", "--play-and-exit"]
        if shuffle and multi:
            argv.append("--random")
        return argv + files
    if has("mpv"):
        argv = ["mpv", "--force-window=yes", "--no-terminal", "--volume=100",
                "--title=Aether — Music"]
        if shuffle and multi:
            argv.append("--shuffle")
        return argv + files
    if has("ffplay"):  # shows a window (no -nodisp)
        return ["ffplay", "-autoexit", "-loglevel", "quiet", files[0]]
    return None


def _stop_existing() -> None:
    try:
        with open(_PIDFILE) as f:
            pid = int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        os.waitpid(pid, 0)  # reap our child so it doesn't linger as a zombie
    except (ChildProcessError, OSError):
        pass
    try:
        os.remove(_PIDFILE)
    except FileNotFoundError:
        pass


@skill("list_music")
def list_music(params):
    if not os.path.isdir(MUSIC_DIR):
        return fail(f"There's no music folder at {MUSIC_DIR}.")

    query = str(params.get("query", "")).strip()
    if query:  # fuzzy, term-based search across the whole tree (tolerant of STT errors)
        ranked = _search(query)
        if not ranked:
            return ok(f"Nothing matched “{query}”. Try fewer or different key words "
                      f"(artist or album), or browse the folders.", tracks=[], count=0, query=query)
        shown = [rel for rel, _ in ranked[:_MAX_LISTED]]
        # show parent-folder/filename so the model sees the album context it can play
        head = "; ".join(os.path.join(os.path.basename(os.path.dirname(r)), os.path.basename(r))
                         for r in shown[:12]) + ("…" if len(ranked) > 12 else "")
        return ok(f"{len(ranked)} match(es) for “{query}”: {head}",
                  tracks=shown, count=len(ranked), query=query)

    rel = str(params.get("path", "")).strip().strip("/")  # browse a directory
    listed = _list_dir(rel)
    if listed is None:
        return fail(f"There's no folder “{rel}” in your music.")
    folders, tracks = listed
    where = rel or "your music folder"
    parts = []
    if folders:
        names = [os.path.basename(f) for f in folders]
        parts.append(f"{len(folders)} folder(s): " + ", ".join(names[:15]) + ("…" if len(folders) > 15 else ""))
    if tracks:
        names = [os.path.basename(t) for t in tracks]
        parts.append(f"{len(tracks)} track(s): " + "; ".join(names[:15]) + ("…" if len(tracks) > 15 else ""))
    if not parts:
        return ok(f"{where} is empty.", path=rel, folders=[], tracks=[])
    return ok(f"In {where} — " + "; ".join(parts),
              path=rel, folders=folders[:_MAX_LISTED], tracks=tracks[:_MAX_LISTED])


@skill("play_music")
def play_music(params):
    if not os.path.isdir(MUSIC_DIR):
        return fail(f"There's no music folder at {MUSIC_DIR}.")
    files = _resolve(params)
    if not files:
        return fail("I couldn't find that in your music folder — use list_music to browse "
                    "or search first, then play one of those paths.")
    argv = _player_argv(files, bool(params.get("shuffle", True)))
    if argv is None:
        return fail("No command-line audio player is installed. Install VLC, mpv, or ffplay.")
    _stop_existing()
    proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    with open(_PIDFILE, "w") as f:
        f.write(str(proc.pid))
    names = [os.path.relpath(f, MUSIC_DIR) for f in files]
    extra = f" and {len(names) - 1} more" if len(names) > 1 else ""
    return ok(f"Playing {names[0]}{extra}.", playing=names[:20], count=len(files))


@skill("stop_playback")
def stop_playback(_):
    _stop_existing()
    return ok("Stopped the music.")