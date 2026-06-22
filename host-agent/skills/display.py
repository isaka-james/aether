"""Display skills: screen brightness.

Tries KDE's PowerDevil over DBus first (best on Plasma), then falls back to
``brightnessctl``, which is desktop-agnostic and works on GNOME, XFCE, sway, and
anywhere with a backlight under /sys/class/backlight.
"""
from __future__ import annotations

from ._util import QDBUS, fail, has, ok, run
from .registry import skill

_SVC = "org.kde.Solid.PowerManagement"
_PATH = "/org/kde/Solid/PowerManagement/Actions/BrightnessControl"
_IFACE = "org.kde.Solid.PowerManagement.Actions.BrightnessControl"


def _bri(method, *args):
    # qdbus wants the member as a single "interface.method" argument.
    return run([QDBUS, _SVC, _PATH, f"{_IFACE}.{method}", *map(str, args)])


def _bctl_percent() -> int | None:
    """Current brightness percentage via brightnessctl, or None."""
    if not has("brightnessctl"):
        return None
    rc, out, _ = run(["brightnessctl", "-m"])      # name,class,current,percent,max
    if rc != 0:
        return None
    for line in out.splitlines():
        f = line.split(",")
        if len(f) >= 4 and f[3].rstrip("%").isdigit():
            return int(f[3].rstrip("%"))
    return None


@skill("get_brightness")
def get_brightness(_):
    rc1, cur, _ = _bri("brightness")
    rc2, mx, _ = _bri("brightnessMax")
    if rc1 == 0 and rc2 == 0 and cur.isdigit() and int(mx or 0) > 0:
        p = round(int(cur) * 100 / int(mx))
        return ok(f"Screen brightness is {p} percent.", percent=p)
    p = _bctl_percent()
    if p is not None:
        return ok(f"Screen brightness is {p} percent.", percent=p)
    return fail("Brightness info isn't available on this display.")


@skill("brightness")
def brightness(params):
    level = max(1, min(100, int(params.get("level", 50))))
    rc, mx, _ = _bri("brightnessMax")
    if rc == 0 and mx.isdigit():
        rc2, _, err = _bri("setBrightness", int(int(mx) * level / 100))
        if rc2 == 0:
            return ok(f"Brightness set to {level} percent.", percent=level)
    if has("brightnessctl"):
        rc2, _, err = run(["brightnessctl", "set", f"{level}%"])
        if rc2 == 0:
            return ok(f"Brightness set to {level} percent.", percent=level)
    return fail("Brightness control isn't available. Install brightnessctl, or your display "
                "may not support software brightness.")
