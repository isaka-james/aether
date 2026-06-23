"""Compatibility shim.

The agent system was split out of this single module into the ``agent`` package
(agent/prompts.py, tools.py, subagents.py, loop.py). This shim keeps the historical import path
working — ``from . import orchestrator`` and ``orchestrator.handle / execute_approved / speak`` —
so the FastAPI app (main.py) and anything else need not change.
"""
from .agent import execute_approved, handle, speak  # noqa: F401

__all__ = ["handle", "execute_approved", "speak"]
