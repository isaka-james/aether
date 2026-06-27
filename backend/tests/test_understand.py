"""Tests for the pure helpers in the understand phase — the objective note that gets folded
into the system prompt, and the context formatter. The model-calling ``refine_request`` is
covered indirectly (it degrades to these on any failure) and not exercised here.
"""
from app.agent.understand import Intent, _format_context, objective_note


def test_objective_note_empty_when_refinement_adds_nothing():
    intent = Intent(goal="lock the screen", refined_request="lock the screen")
    assert objective_note(intent, "lock the screen") == ""
    # case/whitespace-insensitive comparison
    assert objective_note(intent, "  Lock The Screen ") == ""


def test_objective_note_includes_refined_goal():
    intent = Intent(goal="set the system volume to 30", refined_request="turn it down to 30")
    note = objective_note(intent, "turn it down")
    assert "Refined objective" in note
    assert "set the system volume to 30" in note


def test_objective_note_lists_numbered_success_criteria():
    intent = Intent(goal="mute the mic", refined_request="mute the mic",
                    success_criteria=["the default source is muted", "Aether confirms it"])
    note = objective_note(intent, "mute the mic")
    assert "Success criteria" in note
    assert "(1)" in note and "(2)" in note
    assert "the default source is muted" in note


def test_objective_note_includes_multi_step_plan():
    intent = Intent(goal="quiet the music then play jazz", refined_request="...",
                    plan=["stop the current music", "play jazz on youtube"])
    note = objective_note(intent, "music stuff")
    assert "plan" in note.lower()
    assert "play jazz on youtube" in note


def test_objective_note_omits_single_step_plan():
    # A one-step plan is noise — don't inject it.
    intent = Intent(goal="lock the screen", refined_request="lock the screen",
                    plan=["lock the screen"])
    assert objective_note(intent, "lock the screen") == ""


def test_format_context_renders_recent_turns():
    ctx = [
        {"role": "user", "content": "play some jazz"},
        {"role": "assistant", "content": "Playing now, sir."},
    ]
    out = _format_context(ctx)
    assert "User: play some jazz" in out
    assert "You: Playing now, sir." in out


def test_format_context_handles_empty():
    assert _format_context([]) == ""
    assert _format_context(None) == ""
