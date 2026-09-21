"""Output-schema normalization tests."""

from fastmcp.tools.base import Tool

from toolpipe.constants import CONTROL_TAG
from toolpipe.server.schema_transform import (
    MANAGED_TAG,
    VIRTUALIZATION_NOTE,
    normalize_tool,
)


def _structured_tool() -> Tool:
    def get_user(name: str) -> dict:
        """Look up a user."""
        return {"name": name}

    return Tool.from_function(get_user)


def test_input_schema_preserved_output_removed():
    tool = _structured_tool()
    assert tool.output_schema is not None
    normalized = normalize_tool(tool)
    assert normalized.parameters == tool.parameters
    assert normalized.output_schema is None
    assert normalized.name == tool.name
    assert VIRTUALIZATION_NOTE in (normalized.description or "")
    assert "Look up a user." in (normalized.description or "")
    assert MANAGED_TAG in (normalized.tags or set())


def test_original_not_mutated():
    tool = _structured_tool()
    original_schema = tool.output_schema
    original_desc = tool.description
    normalize_tool(tool)
    assert tool.output_schema == original_schema
    assert tool.description == original_desc
    assert MANAGED_TAG not in (tool.tags or set())


def test_note_not_duplicated():
    normalized = normalize_tool(normalize_tool(_structured_tool()))
    assert (normalized.description or "").count(VIRTUALIZATION_NOTE) == 1


def test_control_tools_unaffected():
    def ctrl(ref: str) -> dict:
        return {"ref": ref}

    tool = Tool.from_function(ctrl, tags={CONTROL_TAG})
    assert normalize_tool(tool) is tool
