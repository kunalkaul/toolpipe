"""First-use consent grants and scope persistence.

Scopes: once (one call), session (process), workspace (<storage_dir> file),
always (~/.toolpipe file). Static TOML/imported servers are approved with
scope "config" (read-only base).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from toolpipe.errors import ConfigurationError
from toolpipe.models import CONSENT_SCOPES, POLICY_MODES, ApprovalRecord, ServerConfig

logger = logging.getLogger(__name__)

WORKSPACE_SERVERS_FILE = "servers.json"
ALWAYS_DIR_NAME = ".toolpipe"
ALWAYS_SERVERS_FILE = "servers.json"


def workspace_scope_path(storage_dir: str | Path) -> Path:
    return Path(storage_dir) / WORKSPACE_SERVERS_FILE


def always_scope_path() -> Path:
    return Path.home() / ALWAYS_DIR_NAME / ALWAYS_SERVERS_FILE


def _validate_scope(scope: str) -> str:
    if scope not in CONSENT_SCOPES:
        raise ConfigurationError(f"Scope must be one of {list(CONSENT_SCOPES)}, got {scope!r}.")
    return scope


@dataclass
class ScopeData:
    servers: dict[str, ServerConfig] = field(default_factory=dict)
    policies: dict[str, str] = field(default_factory=dict)


def load_scope_file(path: Path) -> ScopeData:
    """Load persisted approvals + policies; warn-and-skip bad entries, never crash."""
    empty = ScopeData()
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return empty
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("consent file %s unreadable, skipping: %s", path, e)
        return empty
    if not isinstance(data, dict):
        logger.warning("consent file %s malformed, skipping", path)
        return empty
    servers = data.get("servers", {})
    if not isinstance(servers, dict):
        logger.warning("consent file %s malformed, skipping", path)
        return empty
    out = ScopeData()
    for name, spec in servers.items():
        if not isinstance(name, str) or not isinstance(spec, dict):
            logger.warning("consent file %s: skipping bad entry %r", path, name)
            continue
        command, url = spec.get("command"), spec.get("url")
        args, env = spec.get("args", []), spec.get("env", {})
        if (command is None) == (url is None):
            logger.warning("consent file %s: skipping bad entry %r", path, name)
            continue
        if (
            (command is not None and not isinstance(command, str))
            or (url is not None and not isinstance(url, str))
            or not isinstance(args, list)
            or not all(isinstance(a, str) for a in args)
            or not isinstance(env, dict)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items())
        ):
            logger.warning("consent file %s: skipping bad entry %r", path, name)
            continue
        out.servers[name] = ServerConfig(
            name=name, command=command, args=list(args), url=url, env=dict(env)
        )
    policies = data.get("policies", {})
    if not isinstance(policies, dict):
        logger.warning("consent file %s: ignoring malformed policies", path)
    else:
        for target, mode in policies.items():
            if not isinstance(target, str) or mode not in POLICY_MODES:
                logger.warning("consent file %s: skipping bad policy %r", path, target)
                continue
            out.policies[target] = mode
    return out


def save_scope_file(path: Path, data: ScopeData) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "servers": {
            name: {
                "command": cfg.command,
                "args": list(cfg.args),
                "url": cfg.url,
                "env": dict(cfg.env),
            }
            for name, cfg in sorted(data.servers.items())
        },
        "policies": {target: mode for target, mode in sorted(data.policies.items())},
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)


class ConsentStore:
    """Tracks approvals across once/session/workspace/always scopes."""

    def __init__(self, storage_dir: str | Path) -> None:
        self._storage_dir = Path(storage_dir)
        self._once: set[str] = set()
        self._session: set[str] = set()
        self._once_policies: dict[str, str] = {}
        self._session_policies: dict[str, str] = {}
        self._workspace = load_scope_file(workspace_scope_path(self._storage_dir))
        self._always = load_scope_file(always_scope_path())

    def grant(self, config: ServerConfig, scope: str) -> ApprovalRecord:
        scope = _validate_scope(scope)
        if scope == "once":
            self._once.add(config.name)
        elif scope == "session":
            self._session.add(config.name)
        elif scope == "workspace":
            self._workspace.servers[config.name] = config
            save_scope_file(workspace_scope_path(self._storage_dir), self._workspace)
        else:
            self._always.servers[config.name] = config
            save_scope_file(always_scope_path(), self._always)
        return ApprovalRecord(
            name=config.name,
            scope=scope,
            command=config.command,
            args=list(config.args),
            url=config.url,
            env=dict(config.env),
        )

    def is_granted(self, name: str) -> bool:
        return (
            name in self._once
            or name in self._session
            or name in self._workspace.servers
            or name in self._always.servers
        )

    def consume_once(self, name: str) -> bool:
        if name in self._once:
            self._once.discard(name)
            return True
        return False

    def revoke(self, name: str) -> bool:
        removed = False
        for scope_set in (self._once, self._session):
            if name in scope_set:
                scope_set.discard(name)
                removed = True
        for scope_data, path in (
            (self._workspace, workspace_scope_path(self._storage_dir)),
            (self._always, always_scope_path()),
        ):
            if name in scope_data.servers:
                del scope_data.servers[name]
                scope_data.policies = {
                    t: m for t, m in scope_data.policies.items() if not _target_belongs(t, name)
                }
                save_scope_file(path, scope_data)
                removed = True
        return removed

    def persisted_entry(self, name: str) -> ServerConfig | None:
        if name in self._workspace.servers:
            return self._workspace.servers[name]
        return self._always.servers.get(name)

    def workspace_entries(self) -> dict[str, ServerConfig]:
        return dict(self._workspace.servers)

    def always_entries(self) -> dict[str, ServerConfig]:
        return dict(self._always.servers)

    def grant_scope(self, name: str) -> str | None:
        if name in self._once:
            return "once"
        if name in self._session:
            return "session"
        if name in self._workspace.servers:
            return "workspace"
        if name in self._always.servers:
            return "always"
        return None

    def set_policy(self, target: str, mode: str, scope: str) -> None:
        """Persist a virtualization policy target (`server` or `server:tool`)."""
        scope = _validate_scope(scope)
        if mode not in POLICY_MODES:
            raise ConfigurationError(f"Mode must be one of {list(POLICY_MODES)}, got {mode!r}.")
        if scope == "once":
            self._once_policies[target] = mode
        elif scope == "session":
            self._session_policies[target] = mode
        elif scope == "workspace":
            self._workspace.policies[target] = mode
            save_scope_file(workspace_scope_path(self._storage_dir), self._workspace)
        else:
            self._always.policies[target] = mode
            save_scope_file(always_scope_path(), self._always)

    def effective_policy(self, server: str, tool: str | None = None) -> str:
        """Most specific wins: tool target > server target; scope once > session > workspace > always."""
        merged = {
            **self._always.policies,
            **self._workspace.policies,
            **self._session_policies,
            **self._once_policies,
        }
        if tool is not None and f"{server}:{tool}" in merged:
            return merged[f"{server}:{tool}"]
        return merged.get(server, "auto")

    def all_policies(self) -> list[dict[str, str]]:
        """Every configured policy with its winning scope (for get_policy)."""
        seen: dict[str, str] = {}
        for scope_name, policies in (
            ("always", self._always.policies),
            ("workspace", self._workspace.policies),
            ("session", self._session_policies),
            ("once", self._once_policies),
        ):
            for target in policies:
                seen[target] = scope_name
        merged = {
            **self._always.policies,
            **self._workspace.policies,
            **self._session_policies,
            **self._once_policies,
        }
        return [
            {"target": target, "mode": merged[target], "scope": seen[target]}
            for target in sorted(merged)
        ]


def _target_belongs(target: str, server: str) -> bool:
    return target == server or target.startswith(f"{server}:")
