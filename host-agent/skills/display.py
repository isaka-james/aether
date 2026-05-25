"""Display skills: screen brightness via KDE PowerDevil (DBus)."""
from __future__ import annotations

from ._util import QDBUS, fail, ok, run
from .registry import skill

_SVC = "org.kde.Solid.PowerManagement"
_PATH = "/org/kde/Solid/PowerManagement/Actions/BrightnessControl"
_IFACE = "org.kde.Solid.PowerManagement.Actions.BrightnessControl"


def _bri(method, *args):
    # qdbus wants the member as a single "interface.method" argument.
    return run([QDBUS, _SVC, _PATH, f"{_IFACE}.{method}", *map(str, args)])


@skill("get_brightness")
def get_brightness(_):
    rc1, cur, _ = _bri("brightness")
    rc2, mx, _ = _bri("brightnessMax")
    if rc1 == 0 and rc2 == 0 and cur.isdigit() and int(mx or 0) > 0:
        return ok(f"Screen brightness is {round(int(cur) * 100 / int(mx))} percent.",
                  percent=round(int(cur) * 100 / int(mx)))
    return fail("Brightness info isn't available on this display.")


@skill("brightness")
def brightness(params):
    level = max(1, min(100, int(params.get("level", 50))))
    rc, mx, _ = _bri("brightnessMax")
    if rc != 0 or not mx.isdigit():
        return fail("Brightness control isn't available on this display.")
    rc2, _, err = _bri("setBrightness", int(int(mx) * level / 100))
    if rc2 == 0:
        return ok(f"Brightness set to {level} percent.", percent=level)
    return fail("Couldn't set brightness.", error=err)
