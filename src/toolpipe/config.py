"""TOML configuration loading with ${VAR} environment substitution."""

from __future__ import annotations

import logging
import os
import re
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

from toolpipe.errors import ConfigurationError
from toolpipe.models import ResultSettings, ServerConfig, ToolPipeConfig

if TYPE_CHECKING:
    from toolpipe.discovery import DiscoveredServer

logger = logging.getLogger(__name__)

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _substitute_env(value: str) -> str:
    def repl(match: re.Match[str]) -> str:
        var = match.group(1)
        if var not in os.environ:
            raise ConfigurationError(f"Environment variable {var} is not set.")
        return os.environ[var]

    return _ENV_PATTERN.sub(repl, value)


def resolve_env_vars(env: dict[str, str]) -> dict[str, str]:
    """Resolve ${VAR} references now (fail fast on missing variables)."""
    return {k: _substitute_env(v) for k, v in env.items()}


def _substitute_deep(value: object) -> object:
    if isinstance(value, str):
        return _substitute_env(value)
    if isinstance(value, dict):
        return {k: _substitute_deep(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_deep(v) for v in value]
    return value


def load_config(path: str | Path) -> ToolPipeConfig:
    """Load and validate a toolpipe.toml file."""
    path = Path(path)
    try:
        raw = tomllib.loads(path.read_text())
    except FileNotFoundError:
        raise ConfigurationError(f"Config file not found: {path}")
    except tomllib.TOMLDecodeError as e:
        raise ConfigurationError(f"Invalid TOML in {path}: {e}")

    raw = _substitute_deep(raw)
    assert isinstance(raw, dict)

    toolpipe_raw = raw.get("toolpipe", {})
    if not isinstance(toolpipe_raw, dict):
        raise ConfigurationError("[toolpipe] must be a table.")
    name = toolpipe_raw.get("name", "ToolPipe")
    if not isinstance(name, str) or not name:
        raise ConfigurationError("[toolpipe].name must be a non-empty string.")

    results_raw = raw.get("results", {})
    if not isinstance(results_raw, dict):
        raise ConfigurationError("[results] must be a table.")
    unknown = set(results_raw) - set(ResultSettings.__dataclass_fields__)
    if unknown:
        raise ConfigurationError(f"Unknown [results] settings: {sorted(unknown)}.")
    try:
        results = ResultSettings(**results_raw)
    except TypeError as e:
        raise ConfigurationError(f"Invalid [results] setting: {e}")
    for field in (
        "inline_max_bytes",
        "preview_max_bytes",
        "read_max_bytes",
        "max_result_bytes",
        "max_store_bytes",
        "ttl_seconds",
    ):
        if getattr(results, field) < 0:
            raise ConfigurationError(f"[results].{field} must be >= 0.")
    if not results.storage_dir:
        raise ConfigurationError("[results].storage_dir must be a non-empty string.")

    servers_raw = raw.get("servers", {})
    if not isinstance(servers_raw, dict):
        raise ConfigurationError("[servers] must be a table.")
    servers: dict[str, ServerConfig] = {}
    for server_name, spec in servers_raw.items():
        if not isinstance(spec, dict):
            raise ConfigurationError(f"[servers.{server_name}] must be a table.")
        command = spec.get("command")
        url = spec.get("url")
        if command is None and url is None:
            raise ConfigurationError(f"[servers.{server_name}] needs either 'command' or 'url'.")
        if command is not None and url is not None:
            raise ConfigurationError(
                f"[servers.{server_name}] cannot set both 'command' and 'url'."
            )
        if command is not None and not isinstance(command, str):
            raise ConfigurationError(f"[servers.{server_name}.command] must be a string.")
        if url is not None and not isinstance(url, str):
            raise ConfigurationError(f"[servers.{server_name}.url] must be a string.")
        args = spec.get("args", [])
        env = spec.get("env", {})
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise ConfigurationError(f"[servers.{server_name}.args] must be a list of strings.")
        if not isinstance(env, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in env.items()
        ):
            raise ConfigurationError(f"[servers.{server_name}.env] must be string key/values.")
        servers[server_name] = ServerConfig(
            name=server_name, command=command, args=args, url=url, env=dict(env)
        )

    return ToolPipeConfig(name=name, results=results, servers=servers)


def servers_from_discovered(
    entries: list[DiscoveredServer], *, strict: bool, source_label: str = "discovery"
) -> dict[str, ServerConfig]:
    """Convert discovery entries to ServerConfigs.

    Applies ${VAR} substitution. Strict mode (explicit --import-*) raises on
    bad entries; auto-discovery skips them with a warning instead.
    """
    servers: dict[str, ServerConfig] = {}
    for entry in entries:
        try:
            env = {k: _substitute_env(v) for k, v in entry.env.items()}
        except ConfigurationError as e:
            if strict:
                raise
            logger.warning("%s skipped server '%s': %s", source_label, entry.name, e)
            continue
        if entry.command is None and entry.url is None:
            err = ConfigurationError(f"Server '{entry.name}' needs 'command' or 'url'.")
            if strict:
                raise err
            logger.warning("%s skipped server '%s': %s", source_label, entry.name, err)
            continue
        servers[entry.name] = ServerConfig(
            name=entry.name,
            command=entry.command,
            args=list(entry.args),
            url=entry.url,
            env=env,
        )
    return servers


def merge_server_maps(*maps: dict[str, ServerConfig]) -> dict[str, ServerConfig]:
    """Merge server maps; later maps win on name collisions."""
    merged: dict[str, ServerConfig] = {}
    for mapping in maps:
        merged.update(mapping)
    return merged
