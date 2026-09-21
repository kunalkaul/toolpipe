"""toolpipe_search_result: substring/regex search over stored results."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from toolpipe.constants import CONTROL_TAG
from toolpipe.results.manager import ResultManager
from toolpipe.tools._common import translate_errors


def register(mcp: FastMCP, manager: ResultManager) -> None:
    @mcp.tool(
        name="toolpipe_search_result",
        tags={CONTROL_TAG},
        description=(
            "Search a stored result for a substring or regular expression. "
            "Text results return line matches; JSON results return leaf path matches."
        ),
    )
    @translate_errors
    async def toolpipe_search_result(
        ref: str, query: str, regex: bool = False, limit: int = 20
    ) -> dict[str, Any]:
        return manager.search(ref, query, regex=regex, limit=limit)
