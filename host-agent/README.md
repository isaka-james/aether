# Aether host agent

A tiny, **dependency-free** (Python 3 stdlib only) HTTP service that runs natively in
your KDE session. It is the only component that touches the host. The Dockerized
backend sends it validated skills to execute and synthesized speech to play.

## Why a separate agent?

The backend lives in Docker and is isolated from the host by design. But the actual
work (querying Bluetooth, launching apps, controlling volume, playing audio through
your speakers) has to happen on the host, inside your graphical session. The agent
bridges that gap with a small, auditable surface: a curated set of fast skills plus a
screened, token-guarded `run_command` (a full shell, double-checked), so the agent's reach
is broad while the surface stays narrow, and never raw container access to your machine.

It also runs a **notification recorder** (`notify_recorder.py`): a background thread that
watches the session bus with `dbus-monitor` and records desktop notifications as they fire
(KDE exposes no API to read notification *history*, so live capture is the only way). The
`notifications` skill reads them back. Still stdlib-only. `dbus-monitor` is a system tool,
and it's a graceful no-op if there's no session bus.

## Run it

```bash
cd host-agent
AETHER_HOST_AGENT_TOKEN='<same token as backend .env>' python3 agent.py
```

Or install as a `systemd --user` service (recommended, it auto-starts with your
desktop session and inherits DISPLAY/DBUS/audio):

```bash
mkdir -p ~/.config/systemd/user
cp aether-agent.service ~/.config/systemd/user/
# edit the token in the unit to match the backend .env
systemctl --user daemon-reload
systemctl --user enable --now aether-agent
systemctl --user status aether-agent
```

## Security

- Every request must carry `X-Aether-Token` matching `AETHER_HOST_AGENT_TOKEN`.
- Binds `0.0.0.0:8474` so the Docker container can reach it via the host gateway.
  Lock the port down to the docker bridge so it isn't exposed to your whole LAN:

  ```bash
  sudo ufw allow in on docker0 to any port 8474
  sudo ufw deny 8474
  ```

- `run_command` is screened twice: by the backend's safety classifier *and* by a
  hard block list in `skills/shell.py`. Destructive patterns (`rm -rf`, `mkfs`, `dd
  of=/dev/...`, fork bombs, and so on) are refused outright, even with root.
- Root commands: the backend sends the password (`ROOT_PWD`) only on approved sudo
  calls; the agent uses `sudo -S` and never logs or stores it.

## Skills package

Skills live in `skills/`, one module per domain, registered with an `@skill("name")`
decorator and dispatched by `skills.registry.execute()`:

```
skills/
  registry.py   decorator + dispatch
  _util.py      run(), tool discovery, hard-block, result builders
  bluetooth.py  network.py  display.py  media.py  apps.py
  windows.py    inputs.py   system.py   shell.py   browser.py …
notify_recorder.py   background dbus-monitor → notifications.jsonl ring buffer
browser_play.py      detached YouTube worker (real Chrome over CDP)
```

Add a capability by writing a handler in the relevant module:

```python
@skill("my_skill")
def my_skill(params):
    rc, out, err = run(["some", "command"])
    return ok("Done.", value=out) if rc == 0 else fail("Couldn't.", error=err)
```

then add the same name + description to the backend's `app/skills.py` catalog so the
model knows it exists.
