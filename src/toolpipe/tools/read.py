"""toolpipe_read_result: bounded byte-slice reads (diagnostic, not selection)."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from toolpipe.constants import CONTROL_TAG
from toolpipe.results.codec import JSON_CONTENT_TYPE
from toolpipe.results.manager import ResultManager
from toolpipe.tools._common import translate_errors


def register(mcp: FastMCP, manager: ResultManager) -> None:
    @mcp.tool(
        name="toolpipe_read_result",
        tags={CONTROL_TAG},
        description=(
            "Read a bounded byte slice of a stored result as text. "
            "For JSON results this is a diagnostic slice only; "
            "prefer toolpipe_select_result for semantic selection."
        ),
    )
    @translate_errors
    async def toolpipe_read_result(ref: str, offset: int = 0, limit: int = 8192) -> dict[str, Any]:
        result = manager.read(ref, offset=offset, limit=limit)
        payload: dict[str, Any] = {
            "ref": result.ref,
            "offset": result.offset,
            "returned_bytes": result.returned_bytes,
            "has_more": result.has_more,
            "content": result.content,
        }
        if result.content_type == JSON_CONTENT_TYPE:
            payload["note"] = "JSON slice is diagnostic; use toolpipe_select_result."
        return payload
