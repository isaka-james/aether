"""The agent: a multi-step ReAct loop with delegated specialist sub-agents.

Modules:
    prompts    — system prompts + prompt-context builders (time, user, machine, capabilities)
    tools      — backend data tools + the shared single-tool executor (_run_and_observe)
    subagents  — specialist roles, the `delegate` tool, and the headless sub-agent loop
    loop       — the coordinator loop, handle(), execute_approved(), speak()

Public API used by the FastAPI app (and the `orchestrator` compatibility shim).
"""
from .loop import execute_approved, handle, speak  # noqa: F401

__all__ = ["handle", "execute_approved", "speak"]
