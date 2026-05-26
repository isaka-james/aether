"""Pydantic request/response schemas shared across the API."""
from typing import Any, Literal, Optional

from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class Clarification(BaseModel):
    """The user's answer to a multiple-choice question Aether asked when it was unsure."""
    question: str
    answer: str


class TextCommand(BaseModel):
    text: str
    clarify: Optional[Clarification] = None  # set when answering a needs_choice question


class ApproveCommand(BaseModel):
    """Sent by the web client when the user approves a proposed action."""
    skill: str
    params: dict[str, Any] = {}
    transcript: Optional[str] = None


class Action(BaseModel):
    """A structured action chosen by the LLM."""
    skill: str
    params: dict[str, Any] = {}


class CommandResult(BaseModel):
    ok: bool
    status: Literal["done", "blocked", "needs_confirmation", "needs_choice", "error"]
    transcript: Optional[str] = None       # what we heard (voice path)
    skill: Optional[str] = None
    params: dict[str, Any] = {}
    summary: str = ""                       # human-readable result, also spoken
    detail: Optional[str] = None            # extra info / error / block reason
    data: Optional[Any] = None              # structured result payload
    spoken: bool = False                    # whether TTS was played on the host
    question: Optional[str] = None          # needs_choice: the question to ask the user
    options: list[str] = []                 # needs_choice: the choices to offer
