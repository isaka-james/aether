"""Arbitrary shell execution (the escape hatch).

The backend already screens commands; this module re-applies a hard block on the most
destructive patterns and supports approved root execution via ``sudo -S`` with a
password supplied by the backend on the wire (never stored here).
"""
from __future__ import annotations

import re
import subprocess

from config import COMMAND_TIMEOUT

from ._util import HARD_BLOCK, fail, ok
from .registry import skill


@skill("run_command")
def run_command(params):
    command = str(params.get("command", "")).strip()
    if not command:
        return fail("No command was given.")
    if HARD_BLOCK.search(command):
        return fail("That command is blocked for safety.")

    sudo = bool(params.get("sudo"))
    password = params.get("sudo_password")
    if sudo and password:
        inner = re.sub(r"\bsudo\s+", "", command)  # already elevating; drop sudo tokens
        argv, stdin = ["sudo", "-S", "-p", "", "bash", "-lc", inner], password + "\n"
    else:
        argv, stdin = ["/bin/bash", "-lc", command], None

    try:
        p = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=COMMAND_TIMEOUT)
    except subprocess.TimeoutExpired:
        return fail("The command timed out.")

    out = (p.stdout or p.stderr).strip()
    short = out if len(out) <= 600 else out[:600] + "…"
    if p.returncode == 0:
        return ok("Command finished. " + (short[:200] if short else "No output."),
                  returncode=0, output=short, elevated=sudo)
    if sudo and "incorrect password" in out.lower():
        return fail("The root password was rejected.", returncode=p.returncode, elevated=True)
    return fail(f"Command failed (exit {p.returncode}).", returncode=p.returncode, output=short)
