"""toolpipe_release_result: drop a stored result immediately."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from toolpipe.constants import CONTROL_TAG
from toolpipe.results.manager import ResultManager
from toolpipe.tools._common import translate_errors


def register(mcp: FastMCP, manager: ResultManager) -> None:
    @mcp.tool(
        name="toolpipe_release_result",
        tags={CONTROL_TAG},
        description="Release a stored ToolPipe result reference immediately.",
    )
    @translate_errors
    async def toolpipe_release_result(ref: str) -> dict[str, Any]:
        manager.release(ref)
        return {"released": True, "ref": ref}
