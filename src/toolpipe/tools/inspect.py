"""toolpipe_inspect_result: metadata + bounded preview for a stored result."""

from __future__ import annotations

import json
from typing import Any

from fastmcp import FastMCP

from toolpipe.constants import CONTROL_TAG
from toolpipe.results.manager import ResultManager
from toolpipe.tools._common import translate_errors


def register(mcp: FastMCP, manager: ResultManager) -> None:
    @mcp.tool(
        name="toolpipe_inspect_result",
        tags={CONTROL_TAG},
        description="Show metadata and a bounded preview of a stored ToolPipe result reference.",
    )
    @translate_errors
    async def toolpipe_inspect_result(ref: str) -> dict[str, Any]:
        info = manager.inspect(ref)
        return {
            "ref": info.ref,
            "content_type": info.content_type,
            "size_bytes": info.size_bytes,
            "source_tool": info.source_tool,
            "created_at": info.created_at,
            "expires_at": info.expires_at,
            "preview": json.loads(info.preview_json) if info.preview_json else None,
        }
