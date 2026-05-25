# ◈ Aether

A self-hosted voice/text assistant that controls your KDE Linux machine. Speak (or
type) from any device on your network — *"how many Bluetooth devices are connected?"*,
*"open Chrome"*, *"set volume to 30"* — and Aether transcribes it, reasons about it
with the **DeepSeek** API, safely runs it on your machine, and speaks the result back
through your speakers.

Speech (Whisper) and voice (Kokoro) run locally on CPU; the reasoning LLM is DeepSeek's
cloud API (chosen after local CPU inference proved far too slow on low-end hardware).

## How it works

```
  Phone / laptop on the LAN
        │  text or voice (HTTPS/WS)
        ▼
┌─────────────────────────────────────┐        ┌──────────────────────────┐
│  Docker: FastAPI backend (the brain) │        │  Host (native, the hands)│
│  • Web UI + JWT auth                 │        │                          │
│  • Whisper STT (faster-whisper, int8)│──http──▶│  Host agent (stdlib)     │
│  • Kokoro TTS (voice: sky)           │  token │  • executes skills       │
│  • LLM orchestration + safety        │        │  • plays audio (PipeWire)│
│  • Postgres (history/favourites) +   │        │  • Bluetooth/KDE/volume… │
│    Redis (context/cache/fan-out)     │        │  • notification recorder │
└──────────────┬──────────────────────┘        └──────────────────────────┘
        │ https│                                
        ▼      ▼
 host agent   DeepSeek API (deepseek-chat)
  (8765)
```

The backend is sandboxed in Docker; a small **host agent** is the only thing that
touches your machine, exposing a fixed, audited set of skills. See
[`docs/CAPABILITIES.md`](docs/CAPABILITIES.md) for the full feature list and
[`host-agent/README.md`](host-agent/README.md) for the agent's security model.

## Request lifecycle

1. You hold the mic in the web UI and speak (voice is preferred; text also works).
2. The browser uploads the audio; **Whisper** transcribes it to text.
3. **DeepSeek** (`deepseek-chat`, JSON mode) picks one skill + parameters as JSON.
4. The **safety classifier** screens any free-form shell command (block / confirm / allow).
5. The **host agent** runs the skill and returns a raw result.
6. A **second AI pass reviews that result** and phrases the real outcome — confirming
   success with the actual data, or saying plainly if it failed and why.
7. **Kokoro** synthesizes the reply in the *sky* voice; the host agent plays it on your speakers.
8. A "task done" notification is pushed to every connected web client over WebSocket.

## Prerequisites (host)

- Docker + Docker Compose
- A **DeepSeek API key** (https://platform.deepseek.com) → put it in `.env` as `DEEPSEEK_KEY`
- KDE/Plasma tools used by the agent: `bluetoothctl`, `pactl`, `qdbus6`,
  `notify-send`, `spectacle`, `nmcli`, `gtk-launch`, `loginctl`, plus `paplay` or
  `ffplay` for audio. For window/tab/input skills: `wmctrl`, `xdotool`, `xinput`,
  `playerctl` (X11/XWayland).

## Setup

```bash
# 1. Configure
cp .env.example .env
#   edit .env: set a username/password, and pick ONE strong value used in BOTH
#   AETHER_JWT_SECRET and AETHER_HOST_AGENT_TOKEN (the latter must match the agent).

# 2. Start the host agent (runs in your KDE session)
cd host-agent
AETHER_HOST_AGENT_TOKEN='<your token>' python3 agent.py
#   …or install the systemd --user service (see host-agent/README.md)

# 3. Start the backend
cd ..
docker compose up --build
```

Open `http://<this-pc-ip>:8000` from any device on the same network and sign in.
First launch downloads the Whisper and Kokoro models into a Docker volume (one time).

### Accessing the app

- **Locally:** open `http://localhost:8000` (voice works — `localhost` is a secure
  context) or `http://<lan-ip>:8000` from another LAN device (typing only, since plain
  HTTP isn't a secure context for the microphone).
- **Remotely / with voice:** expose it with ngrok, which provides HTTPS so the mic works:
  ```bash
  ngrok http 8000
  ```
  Open the `https://…ngrok…` URL and sign in. The client already uses `wss://` and sends
  the `ngrok-skip-browser-warning` header so live phases work through the tunnel.

> The login (username/password + JWT) is the only thing protecting the app — use a
> strong `AETHER_PASSWORD` before exposing it over a tunnel.

## Resource notes

The heavy reasoning now runs in DeepSeek's cloud, so the host only does STT + TTS on
CPU. Local footprint is small: faster-whisper `tiny` (~0.5 GB) + Kokoro (~0.4 GB).

> History: this started fully local with Ollama, but a 2-core / 12 GB / no-GPU box ran
> 3B–7B models at ~0.4 tok/s (minutes per command). Moving the LLM to DeepSeek dropped
> a command to ~10 s end-to-end. Whisper/Kokoro stay local. The backend needs outbound
> internet to reach `api.deepseek.com`.

Per-command latency is now dominated by Kokoro TTS + playback (a few seconds), not the LLM.

## Layout

```
backend/        FastAPI app (Docker): auth, STT, TTS, LLM orchestration, safety, web UI
  app/          main.py, orchestrator.py, llm.py, stt.py, tts.py, safety.py, skills.py,
                db.py (Postgres), cache.py (Redis), notifications.py (live fan-out) …
  web/          static client (login, push-to-talk, live notifications)
host-agent/     native stdlib service: executes skills + plays audio + records notifications
docs/           CAPABILITIES.md — what Aether can do, and the roadmap
docker-compose.yml   backend + Postgres (db) + Redis (redis)
```

### Persistence (Postgres + Redis)

`docker compose up` also starts **Postgres** (history, notification archive, favourites &
preferences) and **Redis** (follow-up context for "and now mute it", news cache, live
notification fan-out). Both are optional — if either is down the backend logs a warning and
keeps working. Set `POSTGRES_PASSWORD` in `.env`; `GET /api/health` reports `database`/`redis`.
