"""Tests for the skill catalog and the cross-module invariants that depend on it.

The catalog is the single source of truth rendered into the model's prompt and used to
validate tool calls. Several other modules hold hand-maintained lists of skill names
(sub-agent rolesets, the loop's read-only set, the backend-answered tools); if one drifts
out of sync with the catalog a tool silently stops working. These tests pin those
relationships so a typo or a renamed skill fails loudly here instead of in production.
"""
from app.agent.loop import READ_ONLY_TOOLS
from app.agent.subagents import _ROLES
from app.agent.tools import _BACKEND_TOOLS
from app.skills import SKILL_NAMES, SKILLS, catalog_for_prompt


def test_skill_names_are_unique():
    names = [s.name for s in SKILLS]
    assert len(names) == len(set(names)), "duplicate skill name in the catalog"
    assert SKILL_NAMES == set(names)


def test_catalog_lists_every_skill():
    rendered = catalog_for_prompt()
    for name in SKILL_NAMES:
        assert name in rendered, f"{name} missing from the rendered catalog"


def test_catalog_subset_only_includes_requested():
    rendered = catalog_for_prompt({"set_volume"})
    assert "set_volume" in rendered
    assert "weather" not in rendered


def test_impactful_marker_present_for_powerful_skills():
    rendered = catalog_for_prompt({"run_command"})
    assert "[impactful]" in rendered


def test_backend_tools_are_in_the_catalog():
    # Favourites/preferences are answered in the backend but must still appear in the catalog
    # so the model knows it can call them.
    assert _BACKEND_TOOLS <= SKILL_NAMES


def test_read_only_tools_all_exist():
    # Every name the loop treats as read-only must be a real skill or backend tool; otherwise
    # the verify-gate bypass silently never triggers for it.
    known = SKILL_NAMES | _BACKEND_TOOLS
    assert READ_ONLY_TOOLS <= known, READ_ONLY_TOOLS - known


def test_subagent_rolesets_reference_real_skills():
    # A sub-agent's allowed tools are intersected with SKILL_NAMES at spawn time, so a typo here
    # silently removes a tool from that specialist. Catch it.
    for role, (_instruction, tool_names) in _ROLES.items():
        unknown = set(tool_names) - SKILL_NAMES
        assert not unknown, f"role {role!r} lists unknown tools: {unknown}"
