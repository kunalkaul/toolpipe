"""Downstream server registry: known vs approved, mount/unmount, ownership.

Known servers (discovered) spawn nothing until approved. Approved servers are
mounted; mounting only connects on first call, so approval is cheap and
removal is revoke + provider detach (tools vanish on next tools/list).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport
from fastmcp.server.providers.fastmcp_provider import FastMCPProvider
from fastmcp.server.providers.proxy import ProxyProvider
from fastmcp.server.transforms.namespace import Namespace
from fastmcp.tools.base import Tool

from toolpipe.constants import RESERVED_NAMESPACE
from toolpipe.errors import ConfigurationError
from toolpipe.models import ServerConfig
from toolpipe.server.schema_transform import SchemaNormalizationTransform

logger = logging.getLogger(__name__)


def create_proxy(server: ServerConfig) -> FastMCP:
    """Create a FastMCP server that proxies a single downstream server."""
    proxy = FastMCP(f"toolpipe-{server.name}")
    proxy.add_transform(SchemaNormalizationTransform())
    if server.command is not None:
        transport = StdioTransport(
            command=server.command, args=list(server.args), env=dict(server.env) or None
        )
    elif server.url is not None:
        transport = StreamableHttpTransport(url=server.url)
    else:  # validated in config; defensive
        raise ConfigurationError(f"Server {server.name!r} needs 'command' or 'url'.")
    proxy.add_provider(ProxyProvider(lambda _t=transport: Client(_t)))
    return proxy


class ServerRegistry:
    """Owns downstream proxies and maps exposed tool names to servers."""

    def __init__(self) -> None:
        self._servers: dict[str, ServerConfig] = {}
        self._known: dict[str, ServerConfig] = {}
        self._approved: set[str] = set()
        self._revoked: set[str] = set()
        self._one_shot: set[str] = set()
        self._mounted: dict[str, object] = {}
        self._tool_owners: dict[str, str] = {}

    @property
    def server_names(self) -> list[str]:
        return [n for n in self._servers if self._is_routable(n)]

    def known_names(self) -> list[str]:
        return sorted(set(self._servers) | set(self._known))

    def _is_routable(self, name: str) -> bool:
        return name in self._approved and name not in self._revoked

    def register(self, server: ServerConfig) -> None:
        """Register a statically configured (pre-approved) server."""
        self._validate_name(server.name)
        if server.name in self._servers:
            raise ConfigurationError(f"Duplicate server name: {server.name!r}.")
        self._servers[server.name] = server
        self._approved.add(server.name)

    def register_known(self, server: ServerConfig) -> None:
        """Record a discovered server without approving or mounting it."""
        self._validate_name(server.name)
        if server.name not in self._servers:
            self._known[server.name] = server

    def approve(self, name: str, one_shot: bool = False) -> ServerConfig:
        """Mark a known server approved; raises if unknown. Mounting is separate."""
        config = self.known_config(name)
        if config is None:
            raise ConfigurationError(f"Unknown server: {name!r}.")
        self._approved.add(name)
        self._revoked.discard(name)
        if one_shot:
            self._one_shot.add(name)
        return config

    def revoke(self, name: str) -> bool:
        existed = name in self._approved or name in self._servers or name in self._known
        self._approved.discard(name)
        self._one_shot.discard(name)
        self._revoked.add(name)
        return existed

    def consume_one_shot(self, name: str) -> bool:
        if name in self._one_shot:
            self._one_shot.discard(name)
            return True
        return False

    def is_one_shot(self, name: str) -> bool:
        return name in self._one_shot

    def known_config(self, name: str) -> ServerConfig | None:
        if name in self._servers:
            return self._servers[name]
        return self._known.get(name)

    def update_known(self, config: ServerConfig) -> None:
        """Replace a known entry (e.g. with environment-resolved values)."""
        if config.name in self._servers:
            self._servers[config.name] = config
        else:
            self._known[config.name] = config

    def is_known(self, name: str) -> bool:
        return name in self._servers or name in self._known

    def is_approved(self, name: str) -> bool:
        return self._is_routable(name)

    def is_mounted(self, name: str) -> bool:
        return name in self._mounted

    @staticmethod
    def _validate_name(name: str) -> None:
        if name == RESERVED_NAMESPACE:
            raise ConfigurationError(f"Server name {name!r} is reserved.")
        if not name:
            raise ConfigurationError("Server name must be non-empty.")

    def match_server(self, tool_name: str) -> str | None:
        """Longest-prefix server match over known servers (no approval needed)."""
        for name in sorted(set(self._servers) | set(self._known), key=len, reverse=True):
            if tool_name == name or tool_name.startswith(f"{name}_"):
                return name
        return None

    def mount_approved(self, root: FastMCP) -> None:
        for name in sorted(self._servers):
            if self._is_routable(name) and name not in self._mounted:
                self._mount(root, self._servers[name])

    def mount_server(self, root: FastMCP, name: str) -> None:
        """Mount one approved server at runtime (idempotent)."""
        if name in self._mounted or not self._is_routable(name):
            return
        config = self.known_config(name)
        if config is None:
            raise ConfigurationError(f"Unknown server: {name!r}.")
        self._mount(root, config)

    def _mount(self, root: FastMCP, server: ServerConfig) -> None:
        # Mirrors FastMCP.mount(proxy, namespace) exactly, but retains the
        # provider handle so remove_server can detach it again.
        proxy = create_proxy(server)
        provider = FastMCPProvider(proxy)
        namespaced = provider.wrap_transform(Namespace(server.name))
        root.add_provider(namespaced, namespace="")
        self._mounted[server.name] = namespaced
        logger.info("mounted downstream server '%s'", server.name)

    def unmount_server(self, root: FastMCP, name: str) -> bool:
        """Detach a mounted proxy; tools vanish on next tools/list."""
        handle = self._mounted.pop(name, None)
        if handle is None:
            return False
        providers = getattr(root, "providers", None)
        try:
            if providers is not None and handle in providers:
                providers.remove(handle)
                logger.info("unmounted downstream server '%s'", name)
                return True
        except (ValueError, AttributeError) as e:
            logger.warning("provider detach failed for '%s': %s", name, e)
        # Fallback: revoke-only; routing checks approval, so the tools
        # become unroutable even though still listed until restart.
        self._revoked.add(name)
        return False

    async def refresh_tool_map(self, root: FastMCP) -> None:
        """Rebuild exposed-tool -> server map (connects proxies: may spawn servers)."""
        self._tool_owners.clear()
        self._attribute(await root.list_tools())

    def update_map_from_tools(self, tools: Sequence[Tool]) -> None:
        """Rebuild the map from an already-fetched listing (no extra spawn)."""
        self._tool_owners.clear()
        self._attribute(tools)

    def _attribute(self, tools: Sequence[Tool]) -> None:
        # Longest-prefix first so overlapping namespaces (prod vs prod_extra)
        # attribute to the correct owning server. Any approved server counts,
        # whether statically configured or approved at runtime.
        names = sorted((n for n in self._approved if n not in self._revoked), key=len, reverse=True)
        for tool in tools:
            for name in names:
                if tool.name == name or tool.name.startswith(f"{name}_"):
                    self._tool_owners[tool.name] = name
                    break

    def owning_server(self, tool_name: str) -> str | None:
        return self._tool_owners.get(tool_name)

    def tools_of(self, name: str) -> list[str]:
        return [t for t, owner in self._tool_owners.items() if owner == name]

    def is_downstream_tool(self, tool_name: str) -> bool:
        return tool_name in self._tool_owners
