"""What a replace or delete hands back, so the caller can undo it.

Nothing else keeps the old content, so the result is the only undo. Each tool's
own test file checks its copy restores; these cover the shared rules.
"""

from __future__ import annotations

from custom_components.selora_ai.helpers import MAX_RESTORE_CHARS, attach_previous
from custom_components.selora_ai.mcp_server import definitions as mcp_definitions


def test_the_old_content_is_attached_whole() -> None:
    result = attach_previous({"status": "deleted"}, {"alias": "Evening"})

    assert result == {"status": "deleted", "previous": {"alias": "Evening"}}


def test_too_large_is_withheld_not_cut() -> None:
    """A trimmed copy would look restorable and not be."""
    result = attach_previous({"status": "updated"}, {"content": "x" * MAX_RESTORE_CHARS})

    assert "previous" not in result
    assert result["previous_omitted"] is True
    assert "too large" in result["message"]


def test_the_whole_result_is_what_must_fit() -> None:
    """A small copy beside a large result would still be trimmed on the way out."""
    half = "x" * (MAX_RESTORE_CHARS // 2 + 1)

    result = attach_previous({"members": [half]}, {"entities": [half]})

    assert "previous" not in result
    assert result["previous_omitted"] is True


def test_a_failure_carries_nothing() -> None:
    """Nothing was overwritten, so there is nothing to put back."""
    assert attach_previous({"error": "refused"}, {"alias": "Evening"}) == {"error": "refused"}


def test_every_tool_that_returns_it_says_so() -> None:
    tools = {t.name: t for t in mcp_definitions._TOOL_DEFINITIONS}

    for name in mcp_definitions._RETURNS_PREVIOUS | mcp_definitions._RETURNS_STORED:
        description = tools[name].description
        assert "previous" in description, name
        assert "cannot be undone" not in description, name
