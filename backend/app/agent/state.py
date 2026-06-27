"""AgentState — the loop's working memory and explicit phases.

The coordinator loop is a small state machine: discover/plan → execute → verify → stop. This
holds what it needs to carry across turns — the goal and its success criteria, whether it has
actually changed anything yet (which gates verification), a short signature history for
loop-protection, and the reason it stopped — so the behaviour is auditable rather than implicit.
"""
from dataclasses import dataclass, field
from enum import Enum


class Phase(str, Enum):
    PLAN = "plan"          # deciding the approach (first turn of a non-trivial task)
    EXECUTE = "execute"    # running tools and reading observations
    VERIFY = "verify"      # checking the goal's success criteria actually hold
    DONE = "done"          # finished


class StopReason(str, Enum):
    VERIFIED_DONE = "verified_done"    # goal met (and verified, if it changed state)
    ANSWERED = "answered"              # pure question / no state changed
    NEEDS_USER = "needs_user"          # paused for a choice or an approval
    EXHAUSTED = "exhausted"            # ran out of the step budget
    UNRECOVERABLE = "unrecoverable"    # stuck repeating itself with no progress
    LLM_ERROR = "llm_error"            # the model was unreachable


@dataclass
class AgentState:
    goal: str = ""
    success_criteria: list[str] = field(default_factory=list)
    plan: list[str] = field(default_factory=list)    # rough multi-step plan from the refine pass
    requires_action: bool = False                    # does fulfilling this CHANGE machine state?
    phase: Phase = Phase.PLAN
    step: int = 0
    acted: bool = False                              # did a state-CHANGING tool succeed? gates verify
    last_calls: list[str] = field(default_factory=list)   # tool+params signatures, for loop-protection
    repeat_count: int = 0                            # consecutive identical calls
    verify_attempts: int = 0                         # how many times verification sent us back to fix
    action_pushbacks: int = 0                        # times we refused a "done" with no action taken
    stop_reason: "StopReason | None" = None

    def record_call(self, signature: str) -> bool:
        """Append a call signature; return True if it's an immediate repeat of the previous one."""
        repeat = bool(self.last_calls) and signature == self.last_calls[-1]
        self.repeat_count = self.repeat_count + 1 if repeat else 0
        self.last_calls.append(signature)
        return repeat
