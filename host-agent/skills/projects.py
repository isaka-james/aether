"""Project skills: discover the user's code projects under ~/Projects.

Gives the agent a concrete way to answer "what projects do I have?" or to resolve "my
<name> project" to a path, instead of guessing or opening a file manager.
"""
from __future__ import annotations

import os

from config import PROJECTS_DIR
from ._util import fail, ok
from .registry import skill


@skill("list_projects")
def list_projects(_):
    if not os.path.isdir(PROJECTS_DIR):
        return fail(f"There's no projects folder at {PROJECTS_DIR}.")
    names = sorted(e.name for e in os.scandir(PROJECTS_DIR)
                   if e.is_dir() and not e.name.startswith("."))
    if not names:
        return ok(f"No projects found in {PROJECTS_DIR}.", projects=[], count=0)
    return ok(f"{len(names)} project(s) in {PROJECTS_DIR}: " + ", ".join(names) + ".",
              projects=names, count=len(names), dir=PROJECTS_DIR)
