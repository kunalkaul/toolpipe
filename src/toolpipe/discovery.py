"""IDE MCP config auto-discovery.

Scans well-known locations, normalizing every format to ServerConfig entries.
Discovered servers are *known* but never spawned until approved.
"""

from __future__ import annotations

import json
import logging
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscoveredServer:
    name: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    url: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    source: str = ""


def strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments outside string literals."""
    out: list[str] = []
    i, n = 0, len(text)
    in_string = False
    escape = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
        elif ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
        elif ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _mcp_servers_shape(data: dict, source: str) -> list[DiscoveredServer]:
    """Claude/Cursor-style {mcpServers: {name: {command,args,env}|{url}}}."""
    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError(f"{source}: 'mcpServers' must be an object.")
    return [_from_generic(name, spec, source) for name, spec in servers.items()]


def _from_generic(name: object, spec: object, source: str) -> DiscoveredServer:
    if not isinstance(name, str) or not name:
        raise ValueError(f"{source}: server names must be non-empty strings.")
    if not isinstance(spec, dict):
        raise ValueError(f"{source}: server '{name}' must be an object.")
    command = spec.get("command")
    url = spec.get("url")
    args = spec.get("args", [])
    env = spec.get("env", {})
    if command is None and url is None:
        raise ValueError(f"{source}: server '{name}' needs 'command' or 'url'.")
    if command is not None and url is not None:
        raise ValueError(f"{source}: server '{name}' cannot set both.")
    if command is not None and not isinstance(command, str):
        raise ValueError(f"{source}: server '{name}' command must be a string.")
    if url is not None and not isinstance(url, str):
        raise ValueError(f"{source}: server '{name}' url must be a string.")
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ValueError(f"{source}: server '{name}' args must be strings.")
    if not isinstance(env, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in env.items()
    ):
        raise ValueError(f"{source}: server '{name}' env must be string key/values.")
    return DiscoveredServer(
        name=name, command=command, args=list(args), url=url, env=dict(env), source=source
    )


def _opencode_shape(data: dict, source: str) -> list[DiscoveredServer]:
    """OpenCode `mcp` map (also accepts nested `mcp.servers`)."""
    mcp = data.get("mcp", {})
    if not isinstance(mcp, dict):
        raise ValueError(f"{source}: 'mcp' must be an object.")
    servers = mcp.get("servers", mcp)
    if not isinstance(servers, dict):
        raise ValueError(f"{source}: 'mcp' must be an object.")
    out: list[DiscoveredServer] = []
    for name, spec in servers.items():
        if not isinstance(name, str) or not name:
            raise ValueError(f"{source}: server names must be non-empty strings.")
        if not isinstance(spec, dict):
            raise ValueError(f"{source}: server '{name}' must be an object.")
        if spec.get("enabled", True) is False:
            continue
        kind = spec.get("type", "local")
        if kind == "remote":
            url = spec.get("url")
            if not isinstance(url, str):
                raise ValueError(f"{source}: server '{name}' url must be a string.")
            out.append(DiscoveredServer(name=name, url=url, source=source))
            continue
        command = spec.get("command", [])
        if isinstance(command, list):
            if not command or not all(isinstance(c, str) for c in command):
                raise ValueError(f"{source}: server '{name}' command must be strings.")
            cmd, cmd_args = command[0], command[1:]
        elif isinstance(command, str):
            cmd, cmd_args = command, []
        else:
            raise ValueError(f"{source}: server '{name}' command must be a string or list.")
        env = spec.get("environment", {})
        if not isinstance(env, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in env.items()
        ):
            raise ValueError(f"{source}: server '{name}' environment must be strings.")
        out.append(
            DiscoveredServer(name=name, command=cmd, args=cmd_args, env=dict(env), source=source)
        )
    return out


def _zed_shape(data: dict, source: str) -> list[DiscoveredServer]:
    """Zed `context_servers` (command string + args array, or url)."""
    servers = data.get("context_servers", {})
    if not isinstance(servers, dict):
        raise ValueError(f"{source}: 'context_servers' must be an object.")
    out: list[DiscoveredServer] = []
    for name, spec in servers.items():
        if not isinstance(name, str) or not name:
            raise ValueError(f"{source}: server names must be non-empty strings.")
        if not isinstance(spec, dict):
            raise ValueError(f"{source}: server '{name}' must be an object.")
        if "url" in spec and spec["url"] is not None:
            if not isinstance(spec["url"], str):
                raise ValueError(f"{source}: server '{name}' url must be a string.")
            out.append(DiscoveredServer(name=name, url=spec["url"], source=source))
            continue
        entry = dict(spec)
        entry.pop("url", None)
        out.append(_from_generic(name, entry, source))
    return out


def _codex_shape(data: dict, source: str) -> list[DiscoveredServer]:
    """Codex TOML `[mcp_servers.<name>]` tables."""
    servers = data.get("mcp_servers", {})
    if not isinstance(servers, dict):
        raise ValueError(f"{source}: 'mcp_servers' must be a table.")
    out: list[DiscoveredServer] = []
    for name, spec in servers.items():
        if not isinstance(name, str) or not name:
            raise ValueError(f"{source}: server names must be non-empty strings.")
        if not isinstance(spec, dict):
            raise ValueError(f"{source}: server '{name}' must be a table.")
        if spec.get("enabled", True) is False:
            continue
        entry: dict = {
            "command": spec.get("command"),
            "args": spec.get("args", []),
            "url": spec.get("url"),
            "env": spec.get("env", {}),
        }
        out.append(_from_generic(name, entry, source))
    return out


def parse_mcp_json(text: str, source: str) -> list[DiscoveredServer]:
    data = json.loads(strip_jsonc(text))
    if not isinstance(data, dict):
        raise ValueError(f"{source}: top level must be an object.")
    return _mcp_servers_shape(data, source)


def parse_opencode(text: str, source: str) -> list[DiscoveredServer]:
    data = json.loads(strip_jsonc(text))
    if not isinstance(data, dict):
        raise ValueError(f"{source}: top level must be an object.")
    return _opencode_shape(data, source)


def parse_zed(text: str, source: str) -> list[DiscoveredServer]:
    data = json.loads(strip_jsonc(text))
    if not isinstance(data, dict):
        raise ValueError(f"{source}: top level must be an object.")
    return _zed_shape(data, source)


def parse_codex_toml(text: str, source: str) -> list[DiscoveredServer]:
    data = tomllib.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{source}: top level must be a table.")
    return _codex_shape(data, source)


def _read(path: Path) -> str | None:
    try:
        return path.read_text()
    except FileNotFoundError:
        return None
    except OSError as e:
        logger.warning("discovery skipped %s: %s", path, e)
        return None


def discover(workspace_dir: str | Path = ".") -> list[DiscoveredServer]:
    """Scan IDE configs user-level first, then workspace. Never raises.

    Order matters for precedence: later sources override same-named servers,
    so workspace entries win over user-level ones.
    """
    workspace = Path(workspace_dir)
    home = Path.home()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    config_home = Path(xdg) if xdg else home / ".config"

    candidates: list[tuple[Path, str]] = [
        # User files first; workspace files last so they win on collisions.
        (home / ".claude.json", "mcpServers"),
        (home / ".cursor" / "mcp.json", "mcpServers"),
        (home / ".codex" / "config.toml", "codex"),
        (config_home / "opencode" / "opencode.json", "opencode"),
        (config_home / "opencode" / "opencode.jsonc", "opencode"),
        (config_home / "zed" / "settings.json", "zed"),
        # Workspace files (higher precedence).
        (workspace / ".mcp.json", "mcpServers"),
        (workspace / ".cursor" / "mcp.json", "mcpServers"),
        (workspace / ".codex" / "config.toml", "codex"),
        (workspace / "opencode.json", "opencode"),
        (workspace / "opencode.jsonc", "opencode"),
        (workspace / ".zed" / "settings.json", "zed"),
    ]
    found: list[DiscoveredServer] = []
    for path, kind in candidates:
        text = _read(path)
        if text is None:
            continue
        source = str(path)
        try:
            if kind == "mcpServers":
                entries = parse_mcp_json(text, source)
            elif kind == "codex":
                entries = parse_codex_toml(text, source)
            elif kind == "opencode":
                entries = parse_opencode(text, source)
            else:
                entries = parse_zed(text, source)
        except (ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError) as e:
            # Auto-discovery skips bad files with a warning; explicit
            # --import-* flags fail fast instead (see cli.py).
            logger.warning("discovery skipped %s: %s", path, e)
            continue
        for entry in entries:
            logger.info("discovered server '%s' from %s", entry.name, source)
        found.extend(entries)
    return found
