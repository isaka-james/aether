"""Input-device skills (xinput): list devices and enable/disable them (e.g. touchpad).

X11/XWayland only — on pure Wayland xinput sees the XWayland virtual devices."""
from __future__ import annotations

from ._util import fail, need_tool, ok, run
from .registry import skill


@skill("list_input_devices")
def list_input_devices(_):
    if (miss := need_tool("xinput", "Listing input devices")):
        return miss
    rc, out, _ = run(["xinput", "list", "--name-only"])
    if rc != 0:
        return fail("Couldn't list input devices.")
    devices = [d for d in out.splitlines() if d.strip() and "XTEST" not in d]
    return ok(f"{len(devices)} input device(s): " + ", ".join(devices) + ".", devices=devices)


@skill("set_input_device")
def set_input_device(params):
    if (miss := need_tool("xinput", "Changing an input device")):
        return miss
    device = str(params.get("device", "")).strip()
    state = str(params.get("state", "")).lower()
    if not device or state not in ("enable", "disable"):
        return fail("Tell me the device name and whether to enable or disable it.")
    rc, _, err = run(["xinput", state, device])
    verb = "enabled" if state == "enable" else "disabled"
    return ok(f"{verb.capitalize()} {device}.", device=device, state=state) if rc == 0 \
        else fail(f"Couldn't {state} '{device}'.", error=err)
