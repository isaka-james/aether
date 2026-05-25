"""Audio and media skills: volume (PipeWire/PulseAudio via pactl) and media
transport / metadata (via playerctl, falling back to MPRIS over DBus)."""
from __future__ import annotations

from ._util import QDBUS, fail, has, need_tool, ok, run
from .registry import skill

_SINK = "@DEFAULT_SINK@"


@skill("set_volume")
def set_volume(params):
    if "action" in params:
        action = str(params["action"]).lower()
        table = {
            "mute":   (["pactl", "set-sink-mute", _SINK, "1"], "Muted."),
            "unmute": (["pactl", "set-sink-mute", _SINK, "0"], "Unmuted."),
            "up":     (["pactl", "set-sink-volume", _SINK, "+10%"], "Volume up."),
            "down":   (["pactl", "set-sink-volume", _SINK, "-10%"], "Volume down."),
        }
        if action not in table:
            return fail(f"Unknown volume action '{action}'.")
        argv, msg = table[action]
        rc, _, err = run(argv)
        return ok(msg) if rc == 0 else fail("Couldn't change the volume.", error=err)

    level = max(0, min(150, int(params.get("level", 50))))
    rc, _, err = run(["pactl", "set-sink-volume", _SINK, f"{level}%"])
    if rc == 0:
        return ok(f"Volume set to {level} percent.", level=level)
    return fail("Couldn't set the volume.", error=err)


_PLAYERCTL = {"playpause": "play-pause", "play": "play", "pause": "pause",
              "next": "next", "previous": "previous", "prev": "previous", "stop": "stop"}


def _mpris_fallback(action):
    method = {"playpause": "PlayPause", "play": "Play", "pause": "Pause",
              "next": "Next", "previous": "Previous", "prev": "Previous", "stop": "Stop"}.get(action)
    rc, out, _ = run([QDBUS])
    service = next((l.strip() for l in out.splitlines()
                    if l.strip().startswith("org.mpris.MediaPlayer2.")), None)
    if not method or not service:
        return fail("No media player is running.")
    rc, _, err = run([QDBUS, service, "/org/mpris/MediaPlayer2", f"org.mpris.MediaPlayer2.Player.{method}"])
    return ok(f"{action.capitalize()}.") if rc == 0 else fail("Couldn't control the player.", error=err)


@skill("media_control")
def media_control(params):
    action = str(params.get("action", "playpause")).lower()
    if action not in _PLAYERCTL:
        return fail(f"Unknown media action '{action}'.")
    if not has("playerctl"):
        return _mpris_fallback(action)
    rc, _, err = run(["playerctl", _PLAYERCTL[action]])
    if rc == 0:
        return ok(f"{action.capitalize()}.")
    if "No players found" in err:
        return fail("No media player is running.")
    return fail("Couldn't control the media player.", error=err)


@skill("now_playing")
def now_playing(_):
    if (miss := need_tool("playerctl", "Now playing")):
        return miss
    rc, status, _ = run(["playerctl", "status"])
    if rc != 0:
        return ok("Nothing is playing right now.")
    _, meta, _ = run(["playerctl", "metadata", "--format", "{{artist}} — {{title}}"])
    meta = meta.strip(" —")
    if meta:
        return ok(f"{status}: {meta}.", status=status, track=meta)
    return ok(f"The player is {status.lower()}.", status=status)
