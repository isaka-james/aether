"""Shared helpers for skill modules: subprocess execution, tool discovery, the
qdbus binary, the destructive-command block list, and result builders."""
from __future__ import annotations

import re
import shutil
import subprocess

from config import COMMAND_TIMEOUT

# qdbus binary differs across distros (Qt5 vs Qt6).
QDBUS = shutil.which("qdbus6") or shutil.which("qdbus") or "qdbus"

# Defense-in-depth: the backend screens run_command, but we also refuse the most
# destructive patterns here, even when reached with elevated privileges.
HARD_BLOCK = re.compile(
    r"\brm\s+-[a-z]*[rf]|\bmkfs|\bdd\b.*of=/dev/|:\(\)\s*\{|\bshred\b|>\s*/dev/(sd|nvme)",
    re.IGNORECASE,
)


def has(tool: str) -> bool:
    """True if an executable is on PATH."""
    return shutil.which(tool) is not None


def run(argv, timeout: float = COMMAND_TIMEOUT, stdin: str | None = None):
    """Run an argv list. Returns (returncode, stdout, stderr), all stripped."""
    try:
        p = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except FileNotFoundError:
        return 127, "", f"{argv[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"


def ok(summary: str, **data) -> dict:
    return {"ok": True, "summary": summary, "data": data}


def fail(summary: str, **data) -> dict:
    return {"ok": False, "summary": summary, "data": data}


def need_tool(tool: str, feature: str) -> dict | None:
    """Return a failure result if a required tool is missing, else None."""
    if not has(tool):
        return fail(f"{feature} needs '{tool}', which isn't installed.")
    return None
