"""First-use approval grants shared by pipe flow and management tools."""

from __future__ import annotations

import logging

from fastmcp import FastMCP

from toolpipe.errors import ConfigurationError, InvalidPipeTargetError
from toolpipe.models import CONSENT_SCOPES, ServerConfig
from toolpipe.server.consent import ConsentStore
from toolpipe.server.registry import ServerRegistry

logger = logging.getLogger(__name__)


def validate_server_spec(
    name: str,
    command: str | None,
    args: list[str] | None,
    url: str | None,
    env: dict[str, str] | None,
    registry: ServerRegistry,
) -> ServerConfig:
    """Validate a manual server spec like static config (fail fast)."""
    if name in registry.known_names():
        raise InvalidPipeTargetError(f"Server {name!r} is already registered.")
    if command is None and url is None:
        raise InvalidPipeTargetError(f"Server {name!r} needs 'command' or 'url'.")
    if command is not None and url is not None:
        raise InvalidPipeTargetError(f"Server {name!r} cannot set both.")
    args = args or []
    env = env or {}
    if command is not None and not isinstance(command, str):
        raise InvalidPipeTargetError(f"Server {name!r} command must be a string.")
    if url is not None and not isinstance(url, str):
        raise InvalidPipeTargetError(f"Server {name!r} url must be a string.")
    if not all(isinstance(a, str) for a in args):
        raise InvalidPipeTargetError(f"Server {name!r} args must be strings.")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise InvalidPipeTargetError(f"Server {name!r} env must be string key/values.")
    config = ServerConfig(name=name, command=command, args=list(args), url=url, env=dict(env))
    # Reuse registry naming rules (reserved `toolpipe`, non-empty).
    registry.register_known(config)
    return config


async def grant_and_mount(
    root: FastMCP,
    registry: ServerRegistry,
    consent: ConsentStore,
    name: str,
    scope: str,
) -> str:
    """Record a grant (persisting per scope) and mount; refresh the tool map."""
    config = registry.known_config(name)
    if config is None:
        raise InvalidPipeTargetError(f"Unknown server: {name!r}.")
    if scope not in (*CONSENT_SCOPES,):
        raise InvalidPipeTargetError(f"Scope must be one of {list(CONSENT_SCOPES)}.")
    consent.grant(config, scope)
    # Mounted entries get environment resolved now; the persisted file keeps
    # ${VAR} references unresolved (resolved secrets are never written).
    from toolpipe.config import resolve_env_vars

    try:
        resolved_env = resolve_env_vars(config.env)
    except ConfigurationError as e:
        consent.revoke(config.name)
        raise InvalidPipeTargetError(f"Cannot approve '{name}': {e}")
    registry.update_known(
        ServerConfig(
            name=config.name,
            command=config.command,
            args=list(config.args),
            url=config.url,
            env=resolved_env,
        )
    )
    registry.approve(name, one_shot=(scope == "once"))
    # Mount only — no tool listing here, so approval alone spawns nothing.
    # The map fills on next tools/list (middleware hook) or routed call.
    registry.mount_server(root, name)
    logger.info("approved server '%s' scope=%s", name, scope)
    return scope


def describe_server(name: str, config: ServerConfig) -> str:
    if config.command is not None:
        return f"command `{config.command} {' '.join(config.args)}`".rstrip()
    return f"url {config.url}"
