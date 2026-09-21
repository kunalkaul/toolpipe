"""Server management tools: pending/approve/add/remove/list."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from toolpipe.constants import CONTROL_TAG
from toolpipe.errors import InvalidPipeTargetError
from toolpipe.models import CONSENT_SCOPES
from toolpipe.server.approval import describe_server, grant_and_mount, validate_server_spec
from toolpipe.server.consent import ConsentStore
from toolpipe.server.registry import ServerRegistry
from toolpipe.tools._common import translate_errors


def _redacted_env(env: dict[str, str]) -> dict[str, str]:
    return {k: "<set>" if v else "<empty>" for k, v in env.items()}


def register(mcp: FastMCP, registry: ServerRegistry, consent: ConsentStore, dynamic: bool) -> None:
    @mcp.tool(
        name="toolpipe_pending_servers",
        tags={CONTROL_TAG},
        description="List discovered servers waiting for user approval (env redacted).",
    )
    @translate_errors
    async def toolpipe_pending_servers() -> dict[str, Any]:
        pending = []
        for name in registry.known_names():
            if registry.is_approved(name):
                continue
            config = registry.known_config(name)
            if config is None:
                continue
            pending.append({"name": name, "via": describe_server(name, config)})
        return {"pending": pending}

    @mcp.tool(
        name="toolpipe_list_servers",
        tags={CONTROL_TAG},
        description="List downstream servers with scope and status (env redacted).",
    )
    @translate_errors
    async def toolpipe_list_servers() -> dict[str, Any]:
        servers = []
        for name in registry.known_names():
            config = registry.known_config(name)
            if config is None:
                continue
            scope = consent.grant_scope(name) or ("config" if registry.is_approved(name) else None)
            servers.append(
                {
                    "name": name,
                    "via": describe_server(name, config),
                    "env": _redacted_env(config.env),
                    "scope": scope,
                    "approved": registry.is_approved(name),
                    "mounted": registry.is_mounted(name),
                    "tools": len(registry.tools_of(name)),
                }
            )
        return {"servers": servers}

    if not dynamic:
        return

    @mcp.tool(
        name="toolpipe_approve_server",
        tags={CONTROL_TAG},
        description=(
            "Approve a discovered server so ToolPipe proxies it. "
            "Scope: once (this call), session (this process), workspace "
            "(this project), always (everywhere)."
        ),
    )
    @translate_errors
    async def toolpipe_approve_server(name: str, scope: str = "workspace") -> dict[str, Any]:
        if scope not in CONSENT_SCOPES:
            raise InvalidPipeTargetError(f"Scope must be one of {list(CONSENT_SCOPES)}.")
        await grant_and_mount(mcp, registry, consent, name, scope)
        return {"approved": True, "name": name, "scope": scope}

    @mcp.tool(
        name="toolpipe_add_server",
        tags={CONTROL_TAG},
        description=(
            "Manually register a server discovery cannot see, then approve it. "
            "Provide command/args/env or url, plus a scope."
        ),
    )
    @translate_errors
    async def toolpipe_add_server(
        name: str,
        scope: str = "workspace",
        command: str | None = None,
        args: list[str] | None = None,
        url: str | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if scope not in CONSENT_SCOPES:
            raise InvalidPipeTargetError(f"Scope must be one of {list(CONSENT_SCOPES)}.")
        validate_server_spec(name, command, args, url, env, registry)
        await grant_and_mount(mcp, registry, consent, name, scope)
        return {"approved": True, "name": name, "scope": scope}

    @mcp.tool(
        name="toolpipe_remove_server",
        tags={CONTROL_TAG},
        description="Revoke a server: unmount it and delete persisted grants. Stored results are kept.",
    )
    @translate_errors
    async def toolpipe_remove_server(name: str) -> dict[str, Any]:
        if not registry.is_known(name):
            raise InvalidPipeTargetError(f"Unknown server: {name!r}.")
        consent.revoke(name)
        registry.revoke(name)
        registry.unmount_server(mcp, name)
        await registry.refresh_tool_map(mcp)
        return {"removed": True, "name": name}
