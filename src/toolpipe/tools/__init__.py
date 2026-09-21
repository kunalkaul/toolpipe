"""ToolPipe control tools (never auto-virtualized)."""

from __future__ import annotations

from fastmcp import FastMCP

from toolpipe.results.manager import ResultManager
from toolpipe.tools import inspect as inspect_tool
from toolpipe.tools import read as read_tool
from toolpipe.tools import release as release_tool
from toolpipe.tools import search as search_tool
from toolpipe.tools import select as select_tool

__all__ = ["register_control_tools"]


def register_control_tools(mcp: FastMCP, manager: ResultManager) -> None:
    inspect_tool.register(mcp, manager)
    read_tool.register(mcp, manager)
    select_tool.register(mcp, manager)
    search_tool.register(mcp, manager)
    release_tool.register(mcp, manager)
