"""Network skills (NetworkManager via nmcli)."""
from __future__ import annotations

import re

from ._util import fail, ok, run
from .registry import skill

_VIRTUAL_IFACE = re.compile(r"^(lo|docker|br-|virbr|veth|tun|tap|vnet)")


@skill("wifi_status")
def wifi_status(_):
    rc, out, _ = run(["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"])
    if rc != 0:
        return fail("Couldn't read network status.")
    real = [f for line in out.splitlines()
            if len(f := line.split(":")) >= 3 and not _VIRTUAL_IFACE.match(f[2])]
    for name, typ, dev in real:
        if "wireless" in typ:
            return ok(f"Connected to Wi-Fi network {name}.", ssid=name, device=dev)
    for name, typ, dev in real:
        if "ethernet" in typ:
            return ok(f"Connected by Ethernet ({name}).", connection=name, device=dev)
    _, conn, _ = run(["nmcli", "-t", "-f", "CONNECTIVITY", "general"])
    if conn.strip() in ("full", "limited"):
        return ok("Online, but no Wi-Fi interface was detected.", connectivity=conn.strip())
    return ok("Not connected to any network.")


@skill("wifi_power")
def wifi_power(params):
    state = str(params.get("state", "")).lower()
    if state not in ("on", "off"):
        return fail("Tell me whether to turn Wi-Fi on or off.")
    rc, _, err = run(["nmcli", "radio", "wifi", state])
    if rc == 0:
        return ok(f"Wi-Fi turned {state}.", state=state)
    return fail("Couldn't change Wi-Fi.", error=err)
