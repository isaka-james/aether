"""Skill registry.

Each skill is a plain function ``handler(params: dict) -> dict`` registered with the
``@skill("name")`` decorator. Domain modules (bluetooth, display, ...) import this and
decorate their handlers; importing the ``skills`` package registers them all. The HTTP
layer only ever calls :func:`execute`, keeping transport and skills decoupled.
"""
from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger("aether-agent.skills")

Handler = Callable[[dict], dict]
_REGISTRY: dict[str, Handler] = {}


def skill(name: str) -> Callable[[Handler], Handler]:
    """Register a handler under ``name``."""
    def decorator(fn: Handler) -> Handler:
        if name in _REGISTRY:
            raise ValueError(f"duplicate skill registration: {name!r}")
        _REGISTRY[name] = fn
        return fn
    return decorator


def execute(name: str, params: dict | None) -> dict:
    """Dispatch to a registered skill, normalizing errors into a result dict."""
    handler = _REGISTRY.get(name)
    if handler is None:
        return {"ok": False, "summary": f"I don't know how to do '{name}'.", "data": {}}
    try:
        return handler(params or {})
    except Exception as e:  # noqa: BLE001
        log.exception("skill %r failed", name)
        return {"ok": False, "summary": "Something went wrong running that.",
                "data": {"error": str(e)}}


def registered() -> list[str]:
    return sorted(_REGISTRY)
