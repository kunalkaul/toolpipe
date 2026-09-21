"""Programmatic downstream invocation.

Routes through a real in-memory client so mounted-proxy routing,
downstream validation, and the virtualization middleware all apply to
the target call. Isolated here so FastMCP API changes touch one module.
"""

from __future__ import annotations

from typing import Any

from fastmcp import Client, FastMCP
from fastmcp.tools.base import ToolResult


class ToolDispatcher:
    def __init__(self, mcp: FastMCP) -> None:
        self._mcp = mcp

    async def call(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        # raise_on_error stays True: downstream failures must propagate as
        # exceptions, never as silent error payloads.
        async with Client(self._mcp) as client:
            result = await client.call_tool(tool_name, arguments)
        return ToolResult(
            content=list(result.content),
            structured_content=result.structured_content,
        )
