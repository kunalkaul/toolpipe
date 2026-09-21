"""Virtualization policy tools: set/get auto|always|never."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from toolpipe.constants import CONTROL_TAG
from toolpipe.errors import InvalidPipeTargetError
from toolpipe.models import CONSENT_SCOPES, POLICY_MODES
from toolpipe.server.consent import ConsentStore
from toolpipe.server.registry import ServerRegistry
from toolpipe.tools._common import translate_errors


def _split_target(target: str) -> tuple[str, str | None]:
    server, sep, tool = target.partition(":")
    return server, (tool or None) if sep else None


def register(mcp: FastMCP, registry: ServerRegistry, consent: ConsentStore) -> None:
    @mcp.tool(
        name="toolpipe_set_policy",
        tags={CONTROL_TAG},
        description=(
            "Set virtualization policy for a server or server:tool. "
            "auto = size-based (default), always = virtualize even small "
            "results, never = pass through unchanged."
        ),
    )
    @translate_errors
    async def toolpipe_set_policy(
        target: str, mode: str, scope: str = "workspace"
    ) -> dict[str, Any]:
        if mode not in POLICY_MODES:
            raise InvalidPipeTargetError(f"Mode must be one of {list(POLICY_MODES)}.")
        if scope not in CONSENT_SCOPES:
            raise InvalidPipeTargetError(f"Scope must be one of {list(CONSENT_SCOPES)}.")
        server, tool = _split_target(target)
        if not registry.is_known(server):
            raise InvalidPipeTargetError(f"Unknown server: {server!r}.")
        if tool is not None:
            # Targets use the original (pre-namespace) tool name, matching what
            # the middleware resolves. The tool map fills on first listing, so
            # only reject when the server is enumerated and definitively lacks it.
            known_tools = registry.tools_of(server)
            if known_tools and f"{server}_{tool}" not in known_tools:
                raise InvalidPipeTargetError(f"Unknown tool: {target!r}.")
        consent.set_policy(target, mode, scope)
        return {"target": target, "mode": mode, "scope": scope}

    @mcp.tool(
        name="toolpipe_get_policy",
        tags={CONTROL_TAG},
        description="List configured virtualization policies (default is auto).",
    )
    @translate_errors
    async def toolpipe_get_policy(target: str | None = None) -> dict[str, Any]:
        policies = consent.all_policies()
        if target is not None:
            policies = [
                p for p in policies if p["target"] == target or p["target"].startswith(f"{target}:")
            ]
        return {"policies": policies, "default": "auto"}
