"""toolpipe_select_result: JMESPath selection over stored JSON."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from toolpipe.constants import CONTROL_TAG
from toolpipe.results.manager import ResultManager
from toolpipe.tools._common import translate_errors


def register(mcp: FastMCP, manager: ResultManager) -> None:
    @mcp.tool(
        name="toolpipe_select_result",
        tags={CONTROL_TAG},
        description=(
            "Evaluate a JMESPath expression against a stored JSON result. "
            "Example: customers[].revenue. Small selections return inline; "
            "large ones return a new ToolPipe reference."
        ),
    )
    @translate_errors
    async def toolpipe_select_result(ref: str, expression: str) -> dict[str, Any]:
        selected = manager.select(ref, expression)
        if selected.virtualized:
            return {
                "virtualized": True,
                "ref": selected.ref,
                "content_type": selected.content_type,
                "size_bytes": selected.size_bytes,
            }
        return {
            "virtualized": False,
            "content_type": selected.content_type,
            "size_bytes": selected.size_bytes,
            "value": selected.value,
        }
