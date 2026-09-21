"""Automatic result virtualization middleware."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Sequence

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import Tool, ToolResult
from mcp import types as mt
from mcp.types import TextContent

from toolpipe.errors import StorageLimitError, ToolPipeError
from toolpipe.models import ResultSettings, StoredResult
from toolpipe.results import codec
from toolpipe.results.manager import ResultManager
from toolpipe.results.preview import build_preview
from toolpipe.utils.sizes import format_bytes

logger = logging.getLogger(__name__)


def build_reference_tool_result(stored: StoredResult, preview: dict | None) -> ToolResult:
    """Build the compact upstream ToolResult for a stored payload."""
    structured = {
        "toolpipe": {
            "virtualized": True,
            "ref": stored.ref,
            "content_type": stored.content_type,
            "size_bytes": stored.size_bytes,
            "source_tool": stored.source_tool,
            "expires_at": stored.expires_at,
            "preview": preview if preview is not None else {},
        }
    }
    text = (
        "Large result virtualized by ToolPipe.\n"
        f"Reference: {stored.ref}\n"
        f"Size: {format_bytes(stored.size_bytes)}\n"
        "Use ToolPipe result tools to inspect, select, search, or pipe it."
    )
    return ToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=structured,
    )


class ResultVirtualizationMiddleware(Middleware):
    """Store oversized downstream results; return compact `res_*` references."""

    def __init__(
        self,
        manager: ResultManager,
        settings: ResultSettings,
        is_virtualizable: Callable[[str], bool] | None = None,
        owner_of: Callable[[str], str | None] | None = None,
        consume_one_shot: Callable[[str], Awaitable[bool]] | None = None,
        map_updater: Callable[[Sequence[Tool]], None] | None = None,
        policy_of: Callable[[str], str] | None = None,
    ) -> None:
        self._manager = manager
        self._settings = settings
        # Default skips ToolPipe control tools; the app passes a
        # registry-backed predicate for downstream-only virtualization.
        self._is_virtualizable = is_virtualizable or (lambda name: not name.startswith("toolpipe_"))
        self._owner_of = owner_of
        self._consume_one_shot = consume_one_shot
        self._map_updater = map_updater
        self._policy_of = policy_of

    async def on_list_tools(
        self,
        context: MiddlewareContext,
        call_next: CallNext,
    ):
        """Refresh the ownership map from listings (no extra spawn)."""
        tools = await call_next(context)
        if self._map_updater is not None:
            try:
                self._map_updater(tools)
            except Exception as e:  # noqa: BLE001 — map must never break listing
                logger.warning("tool map update failed: %s", e)
        return tools

    async def _consume_if_one_shot(self, tool_name: str) -> None:
        """Consume a one-shot consent grant after a successful call."""
        if self._owner_of is None or self._consume_one_shot is None:
            return
        owner = self._owner_of(tool_name)
        if owner is not None:
            await self._consume_one_shot(owner)

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        result = await call_next(context)
        tool_name: str = context.message.name

        if not self._is_virtualizable(tool_name):
            return result
        self._manager.incr_counter("proxied_tool_calls")
        if result.is_error:
            return result

        mode = self._policy_of(tool_name) if self._policy_of is not None else "auto"
        if mode == "never":
            await self._consume_if_one_shot(tool_name)
            return result

        normalized = codec.normalize(result)
        if not normalized.supported or normalized.payload is None:
            self._manager.incr_counter("unsupported_results")
            await self._consume_if_one_shot(tool_name)
            return result
        if mode != "always" and normalized.size_bytes <= self._settings.inline_max_bytes:
            await self._consume_if_one_shot(tool_name)
            return result

        assert normalized.content_type is not None
        try:
            preview = build_preview(
                normalized.preview_source,
                normalized.content_type,
                self._settings.preview_max_bytes,
            )
            stored = self._manager.store(
                payload=normalized.payload,
                content_type=normalized.content_type,
                source_tool=tool_name,
                preview=preview,
            )
        except StorageLimitError as e:
            # Fail loudly: silently returning the oversized payload would dump
            # it into LLM context — the exact case virtualization exists to
            # prevent. Never return a dangling reference either.
            logger.warning("virtualization rejected tool=%s: %s", tool_name, e)
            raise ToolError(str(e)) from e
        except ToolPipeError as e:
            logger.warning("virtualization skipped tool=%s: %s", tool_name, e)
            return result

        response = build_reference_tool_result(stored, preview)
        await self._consume_if_one_shot(tool_name)
        structured_bytes = len(json.dumps(response.structured_content, ensure_ascii=False).encode())
        text_bytes = sum(
            len(block.text.encode()) for block in response.content if isinstance(block, TextContent)
        )
        self._manager.incr_counter("reference_response_bytes", structured_bytes + text_bytes)
        logger.info("virtualized tool=%s ref=%s bytes=%d", tool_name, stored.ref, stored.size_bytes)
        return response
