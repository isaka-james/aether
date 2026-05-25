"""Bluetooth skills (BlueZ via bluetoothctl)."""
from __future__ import annotations

from ._util import fail, ok, run
from .registry import skill


@skill("bluetooth_status")
def bluetooth_status(_):
    rc, out, _ = run(["bluetoothctl", "devices", "Connected"])
    devices = []
    if rc == 0 and out:
        for line in out.splitlines():
            parts = line.split(" ", 2)
            if len(parts) == 3:
                devices.append(parts[2])
    else:  # older bluetoothctl: inspect each known device
        _, listing, _ = run(["bluetoothctl", "devices"])
        for line in listing.splitlines():
            parts = line.split(" ", 2)
            if len(parts) == 3:
                _, info, _ = run(["bluetoothctl", "info", parts[1]])
                if "Connected: yes" in info:
                    devices.append(parts[2])
    n = len(devices)
    if n == 0:
        return ok("No Bluetooth devices are connected.", count=0, devices=[])
    return ok(f"{n} Bluetooth device{'s' if n != 1 else ''} connected: " + ", ".join(devices) + ".",
              count=n, devices=devices)


@skill("bluetooth_power")
def bluetooth_power(params):
    state = str(params.get("state", "")).lower()
    if state not in ("on", "off"):
        return fail("Tell me whether to turn Bluetooth on or off.")
    rc, _, err = run(["bluetoothctl", "power", state])
    if rc == 0:
        return ok(f"Bluetooth turned {state}.", state=state)
    return fail("Couldn't change Bluetooth.", error=err)
