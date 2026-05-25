# Aether — Capabilities

What the system can do today, and where it's heading.

## Interaction

- **Voice** (preferred): push-to-talk in the web UI → Whisper (`tiny`/`base`, int8, CPU).
- **Text**: type a command and hit enter.
- **Two-pass reasoning**: the model first picks an action, then — after it runs — a
  second pass reviews the *real* result and reports the actual outcome (or why it failed).
- **Spoken replies**: Kokoro TTS in the *sky* voice, played on the **host** speakers.
- **Live task phases**: the web client shows each phase over WebSocket —
  📤 Sending → 📥 Received by Aether → 🧠 Thinking → ⚙️ Running → 🔎 Checking result → 🔊 Speaking.
- **Multi-device**: sign in from any device on the LAN (or via a TLS reverse proxy).

## Skills

Each request is handled by the configured model (DeepSeek) in an agentic loop that chains
one or more skills. The host agent implements them in a modular `skills/` package (one
module per domain, registered via an `@skill` decorator).

| Domain | Skills | Example phrasing |
|---|---|---|
| Bluetooth | `bluetooth_status`, `bluetooth_power` | "turn off bluetooth", "what's connected?" |
| Network | `wifi_status`, `wifi_power` | "turn wifi on", "what network am I on?" |
| Display | `get_brightness`, `brightness` | "what's my brightness?", "dim to 40" |
| Audio / media | `set_volume`, `media_control`, `now_playing` | "mute", "pause", "what song is this?" |
| YouTube (real Chrome over CDP) | `play_youtube`, `stop_youtube`, `youtube_volume`, `youtube_control`, `youtube_status` | "play X on youtube", "turn the video down", "skip this", "what's playing on youtube?" |
| Apps | `open_app`, `close_app`, `running_apps` | "open chrome", "close all my tabs" |
| Windows / tabs | `list_windows`, `count_windows`, `close_window`, `focus_window`, `close_tab`, `new_tab` | "how many windows?", "close this tab", "switch to chrome" |
| Keyboard | `press_keys`, `type_text` | "press ctrl+alt+t", "type hello" |
| Input devices | `list_input_devices`, `set_input_device` | "disable the touchpad" |
| System | `system_info`, `power_profile`, `screenshot`, `lock_screen`, `unlock_screen`, `power_action`, `notifications`, `clear_notifications`, `notify` | "how much RAM is free?", "lock/unlock the screen", "suspend the machine", "what notifications did I miss?" |
| Memory (Postgres) | `list_favorites`, `remember_favorite`, `forget_favorite`, `get_preference`, `set_preference`, `play_history` | "play my favourite song", "remember this as a favourite", "set my usual volume to 30", "what do I play most?" |
| Escape hatch | `run_command` (safety-screened, root-approved) | "list files in my home folder" |
| Chat | `answer` | "who are you?" |

> **Notifications**: KDE has no API to read notification history, so the host agent runs a
> `dbus-monitor` recorder that captures them live. The `notifications` skill reads them; the
> backend archives to Postgres and relays new ones to web clients via Redis.

> Window/tab/keyboard/input skills use `wmctrl`/`xdotool`/`xinput` (X11). On a Wayland
> session they act on XWayland windows only and degrade gracefully otherwise.

## Safety & privileged actions

`run_command` is the only path to free-form shell, screened twice:

1. **Backend classifier** (`backend/app/safety.py`) sorts every command:
   - **block** — destructive/irreversible (`rm -rf`, `mkfs`, `dd of=/dev/*`, fork bombs,
     formatting disks, editing `/etc/passwd`, …). Never runs, even with approval.
   - **confirm** — powerful but legitimate (`sudo`, `shutdown`, deleting files, killing
     processes, package removal). Returned as **needs_confirmation**; you approve it in
     the web UI, which calls `/api/command/approve` (no second LLM pass).
   - **allow** — everything else. Runs directly.
2. **Host agent hard block** (`host-agent/skills/shell.py`) independently refuses the
   worst patterns — even for elevated commands.

**Root execution**: when an approved command uses `sudo`, the backend attaches the host
root password (`ROOT_PWD` from `.env`) to the agent call and the agent runs it with
`sudo -S`. The password travels only backend→agent over the token-authed local channel —
it is **never** included in the result returned to the browser.

Structured skills (`open_app`, `set_volume`, …) are parameterized and bypass the shell.

## Architecture choices

- **Backend in Docker, agent on host.** Isolation by default; the host agent is the
  single narrow, audited bridge to the machine. The container never gets raw host access.
- **Modular skills package** on the agent: `skills/<domain>.py` + a decorator registry;
  the HTTP layer only calls `skills.execute(name, params)`.
- **LLM via DeepSeek** (cloud, OpenAI-compatible). Decision pass uses JSON mode; both
  passes use one configured model (`AETHER_DEEPSEEK_MODEL`, default `deepseek-chat`).
- **Local CPU only for speech**: faster-whisper int8 (STT) and Kokoro ONNX (TTS).
- **Postgres + Redis, both optional.** Postgres persists history/transcripts, the
  notification archive, and favourites/preferences; Redis holds follow-up context, the news
  cache, and notification fan-out. If either is down, the backend warns and keeps working.

## Performance note

The reasoning LLM is cloud-hosted, so per-command latency is ~10 s end-to-end and
dominated by local Kokoro TTS + playback, not the model. Originally fully local with
Ollama, but a 2-core / no-GPU box ran every quant at ~0.4 tok/s (minutes per command),
so the LLM was moved to DeepSeek and Ollama was removed. The backend needs outbound
internet to `api.deepseek.com`.

## Roadmap / possible additions

- ✅ **Conversation memory & follow-ups** ("and now mute it") — short Redis context history.
- ✅ **Audit log** of every command — persisted to Postgres (`interactions`).
- ✅ **Seeing notifications** — live `dbus-monitor` recorder + archive (was DND-only).
- ✅ **Favourites & preferences** — recall "my favourite song" / "my usual volume".
- **Wake word** for hands-free activation (e.g. openWakeWord on CPU).
- **Streaming**: stream TTS audio to the browser as well as the host speakers.
- **Per-skill permissions** on top of the audit log.
- **Native Wayland** window control (kdotool / KWin scripting) to replace the X11 tools.
- **A history/notifications view** in the web UI (data is already persisted).
