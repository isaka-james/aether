"""File search: find the user's files by name (a reliable structured alternative to shell)."""
from __future__ import annotations

import os

from ._util import fail, ok, run
from .registry import skill


@skill("find_files")
def find_files(params):
    """Search the user's files by name. {"query": "resume"} and optional {"dir": "~/Documents"}.
    Searches the home folder by default, skips hidden files, and caps the result."""
    query = str(params.get("query", "")).strip()
    if not query:
        return fail("What file should I look for?")
    base = os.path.expanduser(str(params.get("dir") or "~"))
    if not os.path.isdir(base):
        return fail(f"There's no folder at {base}.")
    rc, out, _ = run(["find", base, "-iname", f"*{query}*", "-not", "-path", "*/.*"], timeout=20)
    files = [line for line in out.splitlines() if line.strip()][:50]
    if not files:
        return ok(f"No files matching '{query}' under {base}.", files=[], query=query)
    names = "; ".join(os.path.basename(f) for f in files[:15]) + ("…" if len(files) > 15 else "")
    return ok(f"{len(files)} file(s) matching '{query}': {names}", files=files, count=len(files), query=query)
