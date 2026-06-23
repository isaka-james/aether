"""Command safety classifier.

Every free-form shell command proposed by the LLM passes through here before it is
allowed anywhere near the host agent. Structured skills (open_app, set_volume, ...)
are inherently safe and bypass this; only the `run_command` skill is screened.

Philosophy — approve, don't dead-end. The user is a trusted owner of their own machine, so
the bar for an outright refusal is deliberately high: only genuinely *catastrophic* commands
(wipe the disk, brick the boot, hand the box to an attacker) are blocked outright. Everything
else that is merely powerful — deleting files, removing packages, touching the firewall, sudo —
is allowed *after one tap of approval* in the UI, with a plain-language reason and a severity so
the user knows what they're signing off on. The aim is "nothing important happens without you,"
not "the assistant keeps saying no."

Classification:
  BLOCK   -> never executed, even with approval (catastrophic / irreversible system damage).
  CONFIRM -> executed only after the user approves it in the UI (powerful but legitimate).
  ALLOW   -> executed directly (read-only / harmless).
"""
import re
from dataclasses import dataclass
from typing import Literal

Verdict = Literal["allow", "confirm", "block"]
Severity = Literal["low", "medium", "high", "critical"]


@dataclass
class SafetyResult:
    verdict: Verdict
    reason: str
    severity: Severity = "low"


# Catastrophic: could wipe a disk, brick the boot, or hand the machine to an attacker. These are
# never run — not even with approval — because a single misheard voice command must not be able
# to destroy the system. Note the targeted `rm` here is only the root/home wipe; an ordinary
# `rm -rf some/dir` is recoverable-by-policy and drops to CONFIRM below.
_BLOCK_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r":\(\)\s*\{\s*:\|\:&\s*\}\s*;\s*:"), "a fork bomb that would freeze the machine"),
    (re.compile(r"\bmkfs(\.\w+)?\b"), "formatting a filesystem"),
    (re.compile(r"\bdd\b.*\bof=/dev/"), "writing raw data over a disk device"),
    (re.compile(r">\s*/dev/(sd|nvme|mmcblk|disk)"), "overwriting a block device"),
    (re.compile(r"\b(fdisk|parted|sgdisk|gdisk)\b"), "rewriting the partition table"),
    (re.compile(r"\brm\s+(-[a-z]*\s+)*(/|~|/\*|\$HOME)\s*$"), "deleting the root or home directory"),
    # `--no-preserve-root` exists only to defeat rm's built-in guard on `/`; its sole purpose is
    # to wipe the root filesystem, so refuse it outright whatever the flag order or target.
    (re.compile(r"--no-preserve-root\b"), "wiping the root filesystem (--no-preserve-root)"),
    # Wiping a whole critical system directory (the dir itself, not a file inside it) bricks the
    # machine irreversibly — block it. `rm -rf /usr/local/foo` still drops to CONFIRM below.
    (re.compile(r"\brm\s+(-[a-z]*\s+)*/(etc|usr|s?bin|lib(32|64)?|boot|var|sys|proc|opt|root|dev|home)/?\s*$"),
     "wiping a critical system directory"),
    # Target `/`: a word char after it (`/usr`), `/*`, or a bare `/` at end of command. The bare
    # `/` case needs the explicit `\s*$` — a trailing `\b` alone never matches `/` at end of line.
    (re.compile(r"\bchmod\s+(-R\s+)?0?[0-7]{3,4}\s+/(?:\b|\*|\s*$)"), "changing permissions on / (root)"),
    (re.compile(r"\bchown\s+(-R\s+)?\S+\s+/(?:\b|\*|\s*$)"), "changing ownership of / (root)"),
    (re.compile(r">\s*/etc/(passwd|shadow|sudoers)"), "overwriting critical authentication files"),
    (re.compile(r"(curl|wget)\s+[^|]*\|\s*(sudo\s+)?(ba)?sh"), "piping a download straight into a shell"),
    (re.compile(r"\b(mv|cp)\s+/\s"), "moving or copying the root directory"),
]

# Powerful but legitimate — run only after the user approves in the UI. (pattern, reason, severity)
# Ordered most-specific/most-impactful first so the surfaced reason is the sharpest one.
_CONFIRM_PATTERNS: list[tuple[re.Pattern, str, Severity]] = [
    (re.compile(r"\brm\s+(-[a-z]*\s+)*(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)"),
     "force-deletes a folder and everything in it", "high"),
    (re.compile(r"\b(shred|wipe)\b"), "irreversibly wipes data", "high"),
    (re.compile(r"\biptables\s+-F\b|\bufw\s+disable\b"), "tears down the firewall", "high"),
    (re.compile(r"\b(userdel|groupdel|deluser)\b"), "deletes a user or group account", "high"),
    (re.compile(r"\bpasswd\b"), "changes an account password", "high"),
    (re.compile(r"\bkill(all)?\s+(-9\s+)?-1\b"), "signals every process (logs you out)", "high"),
    (re.compile(r"\b(rm|rmdir|unlink)\b"), "deletes files", "high"),
    (re.compile(r"\bsudo\b"), "runs with elevated (root) privileges", "medium"),
    (re.compile(r"\b(shutdown|poweroff|reboot|halt)\b"), "powers off or reboots the machine", "medium"),
    (re.compile(r"\bsystemctl\s+(stop|disable|mask|restart)\b"), "stops or disables a system service", "medium"),
    (re.compile(r"\bapt(-get)?\s+(remove|purge|autoremove)\b"), "removes installed packages", "medium"),
    (re.compile(r"\bgit\s+(reset\s+--hard|clean\s+-[a-z]*f|push\s+.*--force)"),
     "performs a destructive git operation", "medium"),
    # Redirecting to a real file overwrites it. Ignore the harmless /dev/null|stderr|stdout
    # and fd-dup (2>&1) cases so read-only investigation (e.g. `find … 2>/dev/null`) runs freely.
    # The target char is `[^\s>]`, not `\S`: otherwise the second `>` of an append (`>> /dev/null`)
    # is itself taken as the "filename", defeating the /dev exclusion for append redirects.
    (re.compile(r">>?\s*(?!&)(?!/dev/(?:null|stderr|stdout)\b)[^\s>]"), "overwrites a file", "medium"),
    (re.compile(r"\bmv\s+\S"), "moves or renames files", "low"),
]


def classify_command(command: str) -> SafetyResult:
    cmd = command.strip()
    if not cmd:
        return SafetyResult("block", "empty command", "low")

    for pattern, reason in _BLOCK_PATTERNS:
        if pattern.search(cmd):
            return SafetyResult("block", reason, "critical")

    for pattern, reason, severity in _CONFIRM_PATTERNS:
        if pattern.search(cmd):
            return SafetyResult("confirm", reason, severity)

    return SafetyResult("allow", "no risky patterns detected", "low")
