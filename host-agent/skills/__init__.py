"""Aether host-agent skills.

Importing this package registers every skill via its module's ``@skill`` decorators.
The HTTP layer uses only :func:`execute` and :func:`registered`.
"""
from .registry import execute, registered  # noqa: F401

# Importing each domain module runs its @skill registrations. Order is irrelevant.
from . import (  # noqa: E402,F401
    apps,
    bluetooth,
    browser,
    camera,
    capabilities,
    clipboard,
    display,
    files,
    inputs,
    media,
    music,
    network,
    news,
    projects,
    shell,
    system,
    weather,
    windows,
)

__all__ = ["execute", "registered"]
