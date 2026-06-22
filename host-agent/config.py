"""Host agent configuration from environment variables. Stdlib only."""
import os

# Must match AETHER_HOST_AGENT_TOKEN in the backend's .env
TOKEN = os.environ.get("AETHER_HOST_AGENT_TOKEN", "please-change-this-shared-secret")

# Bind address. 0.0.0.0 lets the Docker container reach the agent via the host
# gateway. Pair with the shared token (and ideally a firewall rule limiting the
# port to the docker bridge) — see host-agent/README.md.
HOST = os.environ.get("AETHER_AGENT_HOST", "0.0.0.0")
PORT = int(os.environ.get("AETHER_AGENT_PORT", "8474"))

# Max seconds any single host command may run.
COMMAND_TIMEOUT = float(os.environ.get("AETHER_AGENT_CMD_TIMEOUT", "20"))

# --- User content locations (where skills look for the user's own files) ---
MUSIC_DIR = os.path.expanduser(os.environ.get("AETHER_MUSIC_DIR", "~/Music"))
PROJECTS_DIR = os.path.expanduser(os.environ.get("AETHER_PROJECTS_DIR", "~/Projects"))
