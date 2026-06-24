"""Tests for the lenient JSON extractor the agent loop uses on every model turn.

Models don't always return clean JSON — they wrap it in ``` fences or add a stray
sentence. ``_parse`` must recover the object when it reasonably can and return None
(rather than raise) when it can't, because the loop branches on ``isinstance(obj, dict)``.
"""
from app.agent.tools import _parse


def test_clean_object():
    assert _parse('{"tool": "set_volume", "params": {"level": 30}}') == {
        "tool": "set_volume", "params": {"level": 30}}


def test_fenced_object():
    assert _parse('```json\n{"final": "Done."}\n```') == {"final": "Done."}


def test_object_with_surrounding_prose():
    assert _parse('Sure, here you go: {"x": {"y": 2}} hope that helps') == {"x": {"y": 2}}


def test_nested_braces_recovered():
    assert _parse('noise {"a": {"b": {"c": 1}}} more') == {"a": {"b": {"c": 1}}}


def test_not_json_returns_none():
    assert _parse("just some words, no json here") is None


def test_truncated_object_returns_none():
    # Opening brace but no closing one -> can't recover, must be None (not a crash).
    assert _parse('{"a": 1') is None


def test_empty_and_none_inputs():
    assert _parse("") is None
    assert _parse("   ") is None
    assert _parse(None) is None


def test_non_dict_json_passes_through():
    # Valid JSON that isn't an object is returned as-is; callers gate on isinstance(dict).
    assert _parse("[1, 2, 3]") == [1, 2, 3]
    assert _parse('"hello"') == "hello"
