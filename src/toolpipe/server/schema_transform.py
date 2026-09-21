"""Output-schema normalization for proxied downstream tools.

A proxied tool may declare an MCP output schema, but ToolPipe may replace a
large response with a reference envelope that no longer matches it. So the
exposed catalog strips downstream output schemas while preserving input
schemas, names, descriptions (+ virtualization note), and behavior.

Implemented as a copy layer (TransformedTool); originals are never mutated.
"""

from __future__ import annotations

from collections.abc import Sequence

from fastmcp.server.transforms import Transform
from fastmcp.tools.base import Tool
from fastmcp.tools.tool_transform import TransformedTool

from toolpipe.constants import CONTROL_TAG

VIRTUALIZATION_NOTE = "Large results may be returned as a ToolPipe result reference."
MANAGED_TAG = "toolpipe-managed"


def normalize_tool(tool: Tool) -> Tool:
    """Return a copy of a downstream tool with output schema removed.

    Control tools (tagged `toolpipe-control`) are returned unchanged.
    """
    if CONTROL_TAG in (tool.tags or set()):
        return tool
    description = tool.description or ""
    if VIRTUALIZATION_NOTE not in description:
        description = f"{description}\n\n{VIRTUALIZATION_NOTE}".strip()
    tags = set(tool.tags or set()) | {MANAGED_TAG}
    if tool.output_schema is None and tool.description == description and tags == tool.tags:
        return tool
    return TransformedTool.from_tool(
        tool,
        output_schema=None,
        description=description,
        tags=tags,
    )


class SchemaNormalizationTransform(Transform):
    """Strip output schemas from all tools passing through this server."""

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [normalize_tool(t) for t in tools]

    async def get_tool(self, name: str, call_next, *, version=None) -> Tool | None:
        tool = await call_next(name, version=version)
        return normalize_tool(tool) if tool is not None else None
