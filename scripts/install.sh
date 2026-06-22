#!/usr/bin/env bash
# ============================================================================
#  Aether installer
# ============================================================================
# One command to set Aether up on your KDE desktop. It asks a few simple
# questions, writes your settings, and starts everything. Safe to run again.
#
#   bash scripts/install.sh
#
# Run it from inside your KDE session (open a Konsole window) so the assistant
# can reach your screen, sound, and apps.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
EXAMPLE="$ROOT/.env.example"
AUTOSTART_FILE="$HOME/.config/autostart/aether.desktop"

if [ -t 1 ]; then B=$'\033[1m'; C=$'\033[36m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; N=$'\033[0m'; else B= C= G= Y= R= N=; fi
say()  { printf '\n%s%s%s\n' "$C" "$*" "$N"; }
ok()   { printf '%s  ok%s  %s\n' "$G" "$N" "$*"; }
warn() { printf '%s  !%s   %s\n' "$Y" "$N" "$*"; }
err()  { printf '%s  x%s   %s\n' "$R" "$N" "$*" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }
ask()  { local p="$1" d="${2:-}" a; read -r -p "$(printf '%s%s%s ' "$B" "$p" "$N")" a || true; printf '%s' "${a:-$d}"; }
yes()  { local a; a="$(ask "$1" "$2")"; case "$a" in y|Y|yes|YES) return 0;; *) return 1;; esac; }

printf '\n%s  Aether installer%s\n' "$B" "$N"
printf '  Your own voice assistant for KDE. Lets get it running.\n'

# --- 1. Prerequisites -------------------------------------------------------
say "Step 1 of 4   Checking what you need"
MISSING=0
if have docker && docker compose version >/dev/null 2>&1; then ok "Docker and Compose found"
else err "Docker is not installed. Get it here, then run this again:"; echo "        https://docs.docker.com/engine/install/"; MISSING=1; fi
have python3 || { err "python3 is not installed."; MISSING=1; }
have openssl || { err "openssl is not installed (needed to make passwords)."; MISSING=1; }
[ "$MISSING" -eq 0 ] || { err "Please install the items above, then run this again."; exit 1; }
have google-chrome || have google-chrome-stable || have chromium || warn "Google Chrome was not found. It is only needed for playing YouTube."

# --- 2. Your settings -------------------------------------------------------
say "Step 2 of 4   Your account and AI model"
get_env() { [ -f "$ENV_FILE" ] && grep -E "^$1=" "$ENV_FILE" | head -n1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//; s/[[:space:]]+$//' || true; }
set_env() {
  AE_K="$1" AE_V="$2" python3 - "$ENV_FILE" <<'PY'
import os, re, sys
path, k, v = sys.argv[1], os.environ["AE_K"], os.environ["AE_V"]
lines = open(path).read().splitlines() if os.path.exists(path) else []
pat, done, out = re.compile(rf'^\s*{re.escape(k)}='), False, []
for ln in lines:
    out.append(f"{k}={v}") if pat.match(ln) and not done else out.append(ln)
    done = done or pat.match(ln) is not None
if not done: out.append(f"{k}={v}")
open(path, "w").write("\n".join(out) + "\n")
PY
}
gen_secret() { local cur; cur="$(get_env "$1")"; case "$cur" in ""|*change-me*|*your-*) set_env "$1" "$(openssl rand -hex 32)";; esac; }

CONFIGURE=yes
if [ -f "$ENV_FILE" ]; then
  yes "You already have settings. Change your login or AI model? [y/N]:" n || CONFIGURE=no
else
  cp "$EXAMPLE" "$ENV_FILE"; ok "Created your settings file (.env)"
fi
# Always make sure the secret keys are strong, never placeholders.
gen_secret AETHER_JWT_SECRET
gen_secret AETHER_HOST_AGENT_TOKEN
case "$(get_env POSTGRES_PASSWORD)" in ""|aether) set_env POSTGRES_PASSWORD "$(openssl rand -hex 16)";; esac

if [ "$CONFIGURE" = yes ]; then
  set_env AETHER_USERNAME "$(ask 'Pick a username:' "$(get_env AETHER_USERNAME || echo admin)")"
  printf '%sPick a password%s (just press Enter to get a strong one): ' "$B" "$N"; read -rs pw; echo
  [ -n "$pw" ] || { pw="$(openssl rand -base64 12 | tr -d '/+=' | cut -c1-14)"; warn "Your password is: ${B}${pw}${N}   (write it down now)"; }
  set_env AETHER_PASSWORD "$pw"

  printf '\n  Which AI model should run the assistant?\n'
  printf '    1) DeepSeek   cheap and fast, runs in the cloud (recommended)\n'
  printf '    2) OpenAI     runs in the cloud\n'
  printf '    3) Claude     by Anthropic, runs in the cloud\n'
  printf '    4) Local      runs on your own computer, no key needed\n'
  printf '    5) Leave it as it is\n'
  case "$(ask 'Type 1, 2, 3, 4 or 5:' 1)" in
    1) set_env AETHER_LLM_PROVIDER deepseek; set_env AETHER_LLM_MODEL deepseek-chat
       k="$(ask 'Paste your DeepSeek key (from platform.deepseek.com):' "$(get_env DEEPSEEK_API_KEY)")"; [ -n "$k" ] && set_env DEEPSEEK_API_KEY "$k";;
    2) set_env AETHER_LLM_PROVIDER openai; set_env AETHER_LLM_MODEL "$(ask 'Model name:' gpt-4o-mini)"
       k="$(ask 'Paste your OpenAI key:' "$(get_env OPENAI_API_KEY)")"; [ -n "$k" ] && set_env OPENAI_API_KEY "$k";;
    3) set_env AETHER_LLM_PROVIDER anthropic; set_env AETHER_LLM_MODEL "$(ask 'Model name:' claude-opus-4-8)"
       k="$(ask 'Paste your Anthropic key:' "$(get_env ANTHROPIC_API_KEY)")"; [ -n "$k" ] && set_env ANTHROPIC_API_KEY "$k";;
    4) set_env AETHER_LLM_PROVIDER local; set_env AETHER_LLM_MODEL "$(ask 'Local model name:' llama3.1)"
       warn "Start your local model first, for example: ollama serve. More in docs/PROVIDERS.md";;
    *) :;;
  esac

  printf '\n  A few optional details (press Enter to skip):\n'
  set_env AETHER_USER_NAME "$(ask 'Your name (so it can greet you):' "$(get_env AETHER_USER_NAME)")"
  set_env AETHER_USER_CITY "$(ask 'Your city:' "$(get_env AETHER_USER_CITY)")"
  set_env AETHER_USER_COUNTRY "$(ask 'Your country:' "$(get_env AETHER_USER_COUNTRY)")"
  tz="$(get_env AETHER_TZ)"; [ -n "$tz" ] || { tz="$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || true)"; [ -n "$tz" ] && set_env AETHER_TZ "$tz"; }
fi
chmod 600 "$ENV_FILE" 2>/dev/null || true
ok "Settings saved"

# --- 3. What to set up (your choices) ---------------------------------------
say "Step 3 of 4   What would you like to set up?"
printf '  Press Enter to accept the suggestion in capitals.\n\n'
OPT_YT=no; [ -d "$ROOT/host-agent/.venv" ] && OPT_YT=skip
if [ "$OPT_YT" != skip ] && yes 'Play music and videos on YouTube? [Y/n]:' y; then OPT_YT=yes; fi
OPT_INPUT=no; yes 'Smooth typing on Wayland without permission popups? Needs your sudo password. [y/N]:' n && OPT_INPUT=yes
OPT_BOOT=no;  yes 'Start Aether automatically every time you log in? [Y/n]:' y && OPT_BOOT=yes
OPT_NOW=no;   yes 'Start Aether right now? [Y/n]:' y && OPT_NOW=yes

# --- 4. Apply ---------------------------------------------------------------
say "Step 4 of 4   Setting things up"
chmod +x "$ROOT/scripts/"*.sh 2>/dev/null || true

if [ "$OPT_YT" = yes ]; then
  bash "$ROOT/scripts/setup-browser.sh" >/dev/null 2>&1 && ok "YouTube playback ready" || warn "YouTube setup did not finish. You can run scripts/setup-browser.sh later."
elif [ "$OPT_YT" = skip ]; then ok "YouTube playback already set up"
else warn "Skipped YouTube. Run scripts/setup-browser.sh later to add it."; fi

if [ "$OPT_INPUT" = yes ]; then
  sudo bash "$ROOT/scripts/setup-input.sh" && ok "Smooth typing ready" || warn "Smooth typing setup did not finish (you can skip this)."
fi

if [ "$OPT_BOOT" = yes ]; then
  mkdir -p "$(dirname "$AUTOSTART_FILE")"
  cat > "$AUTOSTART_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Aether
Comment=Starts the Aether assistant when you log in
Exec=/usr/bin/env bash "$ROOT/scripts/aether-keepalive.sh"
Terminal=false
X-GNOME-Autostart-enabled=true
X-KDE-autostart-after=panel
EOF
  ok "Aether will start on every login"
  if have systemctl && [ "$(systemctl is-enabled docker 2>/dev/null)" != "enabled" ]; then
    warn "Tip: let Docker start on boot too, so the backend comes up by itself:"
    printf '         sudo systemctl enable --now docker\n'
  fi
else
  rm -f "$AUTOSTART_FILE" 2>/dev/null || true
  warn "Not starting on login. Start it yourself anytime with scripts/aether-up.sh"
fi

if [ "$OPT_NOW" = yes ]; then
  if docker info >/dev/null 2>&1; then
    setsid bash "$ROOT/scripts/aether-keepalive.sh" >/dev/null 2>&1 < /dev/null &
    printf '  Starting up. The first run downloads a few things, so give it a few minutes.\n'
    PORT="$(get_env AETHER_AGENT_PORT)"; PORT="${PORT:-8474}"
    for _ in $(seq 1 120); do curl -fsS -m 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break; sleep 2; done
    curl -fsS -m 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && ok "Aether is running" || warn "Still starting. Give it a minute, then open the page below."
  else
    warn "Docker is not running. Start it with: sudo systemctl start docker, then run scripts/aether-up.sh"
  fi
fi

IP="$(ip route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}')"; IP="${IP:-your-pc-ip}"
WEB="$(get_env AETHER_WEB_PORT)"; WEB="${WEB:-8473}"
printf '\n%s  All done.%s\n\n' "$G" "$N"
printf '  Open Aether in your browser:\n'
printf '    On this computer:   http://localhost:%s\n' "$WEB"
printf '    Another device:     http://%s:%s   (typing works here)\n' "$IP" "$WEB"
printf '    From anywhere:      run  ngrok http %s  and open the https link\n\n' "$WEB"
printf '  Talking by voice on a phone needs https, so use the ngrok link there (typing\n'
printf '  still works on plain http). In your browser menu, choose Install or Add to Home\n'
printf '  Screen to use Aether like a normal app.\n\n'
printf '  Sign in with the username and password you chose.\n'
printf '  Stop it:  scripts/aether-down.sh      Change the AI model:  docs/PROVIDERS.md\n\n'
