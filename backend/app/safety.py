"""Command safety classifier.

Every free-form shell command proposed by the LLM passes through here before it is
allowed anywhere near the host agent. Structured skills (open_app, set_volume, ...)
are inherently safe and bypass this; only the `run_command` skill is screened.

Classification:
  BLOCK   -> never executed, regardless of confirmation.
  CONFIRM -> executed only if the user explicitly confirms in the UI.
  ALLOW   -> executed directly.
"""
import re
from dataclasses import dataclass
from typing import Literal

Verdict = Literal["allow", "confirm", "block"]


@dataclass
class SafetyResult:
    verdict: Verdict
    reason: str


# Patterns that are destructive, irreversible, or system-compromising — always blocked.
_BLOCK_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\brm\s+(-[a-z]*\s+)*(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)"), "recursive force delete (rm -rf)"),
    (re.compile(r"\brm\s+(-[a-z]*\s+)*(/|~|/\*|\$HOME)\s*$"), "deleting a root/home path"),
    (re.compile(r"\bmkfs(\.\w+)?\b"), "formatting a filesystem"),
    (re.compile(r"\bdd\b.*\bof=/dev/"), "writing raw data to a device"),
    (re.compile(r"\b(shred|wipe)\b"), "secure-wiping data"),
    (re.compile(r">\s*/dev/(sd|nvme|mmcblk|disk)"), "overwriting a block device"),
    (re.compile(r":\(\)\s*\{\s*:\|\:&\s*\}\s*;\s*:"), "fork bomb"),
    (re.compile(r"\b(fdisk|parted|sgdisk|gdisk)\b"), "partition table modification"),
    (re.compile(r"\bchmod\s+(-R\s+)?0?[0-7]{3,4}\s+/\b"), "changing permissions on root"),
    (re.compile(r"\bchown\s+(-R\s+)?\S+\s+/\b"), "changing ownership of root"),
    (re.compile(r"\buserdel\b|\bgroupdel\b|\bdeluser\b"), "deleting accounts"),
    (re.compile(r"\bpasswd\b"), "changing passwords"),
    (re.compile(r"(curl|wget)\s+[^|]*\|\s*(sudo\s+)?(ba)?sh"), "piping a download straight into a shell"),
    (re.compile(r"\bkill(all)?\s+(-9\s+)?-1\b"), "killing all processes"),
    (re.compile(r"\b(mv|cp)\s+/\s"), "moving/copying the root directory"),
    (re.compile(r">\s*/etc/(passwd|shadow|sudoers)"), "overwriting critical system files"),
    (re.compile(r"\biptables\s+-F\b|\bufw\s+disable\b"), "tearing down the firewall"),
]

# Patterns that are powerful but legitimate — allowed only after explicit confirmation.
_CONFIRM_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bsudo\b"), "runs with elevated privileges"),
    (re.compile(r"\b(shutdown|poweroff|reboot|halt)\b"), "powers off or reboots the machine"),
    (re.compile(r"\bsystemctl\s+(stop|disable|mask|restart)\b"), "stops or disables a system service"),
    (re.compile(r"\b(rm|rmdir|unlink)\b"), "deletes files"),
    (re.compile(r"\b(kill|killall|pkill)\b"), "terminates processes"),
    (re.compile(r"\bapt(-get)?\s+(remove|purge|autoremove)\b"), "removes installed packages"),
    (re.compile(r"\bgit\s+(reset\s+--hard|clean\s+-[a-z]*f|push\s+.*--force)"), "performs a destructive git operation"),
    # Redirecting to a real file overwrites it. Ignore the harmless /dev/null|stderr|stdout
    # and fd-dup (2>&1) cases so read-only investigation (e.g. `find … 2>/dev/null`) runs freely.
    (re.compile(r">>?\s*(?!&)(?!/dev/(?:null|stderr|stdout)\b)\S"), "overwrites a file"),
    (re.compile(r"\bmv\s+\S"), "moves/renames files"),
]


def classify_command(command: str) -> SafetyResult:
    cmd = command.strip()
    if not cmd:
        return SafetyResult("block", "empty command")

    for pattern, reason in _BLOCK_PATTERNS:
        if pattern.search(cmd):
            return SafetyResult("block", reason)

    for pattern, reason in _CONFIRM_PATTERNS:
        if pattern.search(cmd):
            return SafetyResult("confirm", reason)

    return SafetyResult("allow", "no risky patterns detected")
