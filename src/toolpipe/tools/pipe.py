"""toolpipe_pipe_result: pipe stored data directly into a downstream tool.

Unknown servers trigger the first-use consent flow: MCP elicitation
when the client supports it, otherwise a guided error naming
toolpipe_approve_server.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastmcp import Context, FastMCP
from fastmcp.tools.base import ToolResult

from toolpipe.constants import CONTROL_TAG
from toolpipe.errors import InvalidPipeTargetError, SelectionError
from toolpipe.results import selector
from toolpipe.results.codec import JSON_CONTENT_TYPE
from toolpipe.results.manager import ResultManager
from toolpipe.server.approval import describe_server, grant_and_mount
from toolpipe.server.consent import ConsentStore
from toolpipe.server.dispatcher import ToolDispatcher
from toolpipe.server.registry import ServerRegistry
from toolpipe.tools._common import translate_errors

logger = logging.getLogger(__name__)

CONSENT_OPTIONS = ["once", "session", "workspace", "always", "deny"]


async def _elicit_scope(ctx: Context | None, server: str, detail: str) -> str | None:
    """Ask the user for a consent scope. None = client cannot elicit."""
    if ctx is None:
        return None
    try:
        result = await ctx.elicit(
            f"Allow ToolPipe to proxy MCP server '{server}' ({detail})?"
            " This spawns its process and routes its tool calls through ToolPipe.",
            CONSENT_OPTIONS,
        )
    except Exception as e:  # noqa: BLE001 — any elicitation failure means fallback
        logger.info("elicitation unsupported for '%s': %s", server, e)
        return None
    action = getattr(result, "action", None)
    if action != "accept":
        return "deny"
    data = getattr(result, "data", None)
    return data if data in CONSENT_OPTIONS else "deny"


async def _ensure_routable(
    mcp: FastMCP,
    registry: ServerRegistry,
    consent: ConsentStore,
    target_tool: str,
    ctx: Context | None,
) -> None:
    """Approve-on-first-use for unapproved servers; error otherwise."""
    server = registry.match_server(target_tool)
    if server is None:
        raise InvalidPipeTargetError(
            f"`{target_tool}` cannot be used as a pipe target. If this is a new "
            "server, register it with toolpipe_add_server first."
        )
    if registry.is_approved(server):
        registry.mount_server(mcp, server)
        await registry.refresh_tool_map(mcp)
        return
    config = registry.known_config(server)
    if config is None:  # defensive; match_server implies known
        raise InvalidPipeTargetError(f"`{target_tool}` cannot be used as a pipe target.")
    scope = await _elicit_scope(ctx, server, describe_server(server, config))
    if scope is None:
        raise InvalidPipeTargetError(
            f"Server '{server}' is not approved. Your client does not support "
            "approval prompts — ask the user, then call toolpipe_approve_server "
            f"with scope once|session|workspace|always and retry."
        )
    if scope == "deny":
        raise InvalidPipeTargetError(f"Server '{server}' was not approved by the user.")
    await grant_and_mount(mcp, registry, consent, server, scope)


def _resolve_mapping_value(
    expression: str, parsed_json: Any, text: str | None, is_json: bool
) -> Any:
    if is_json:
        return selector.evaluate(parsed_json, expression)
    if expression == "@":
        return text
    raise SelectionError(
        f"JMESPath expression `{expression}` requires JSON; text results only support `@`."
    )


def register(
    mcp: FastMCP,
    manager: ResultManager,
    dispatcher: ToolDispatcher,
    registry: ServerRegistry,
    consent: ConsentStore,
) -> None:
    @mcp.tool(
        name="toolpipe_pipe_result",
        tags={CONTROL_TAG},
        description=(
            "Pipe data from a stored result directly into a downstream tool. "
            "Mapping values are JMESPath expressions over the stored JSON "
            "(`@` passes the whole payload); extra literal arguments are allowed. "
            "First use of a new server asks for your approval."
        ),
    )
    @translate_errors
    async def toolpipe_pipe_result(
        ref: str,
        target_tool: str,
        mapping: dict[str, str],
        arguments: dict[str, Any] | None = None,
        ctx: Context | None = None,
    ) -> ToolResult:
        if not registry.is_downstream_tool(target_tool):
            await _ensure_routable(mcp, registry, consent, target_tool, ctx)
        stored, raw = manager.payload_text(ref)
        is_json = stored.content_type == JSON_CONTENT_TYPE
        parsed_json = json.loads(raw) if is_json else None

        literal = arguments or {}
        collision = set(mapping) & set(literal)
        if collision:
            raise InvalidPipeTargetError(
                f"Arguments {sorted(collision)} appear in both mapping and arguments."
            )
        target_args: dict[str, Any] = {}
        for key, expression in mapping.items():
            target_args[key] = _resolve_mapping_value(expression, parsed_json, raw, is_json)
        target_args.update(literal)

        result = await dispatcher.call(target_tool, target_args)
        manager.incr_counter("pipe_calls")
        return result
