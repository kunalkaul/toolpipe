"""Root ToolPipe FastMCP application: config + consent + registry + mount."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastmcp import FastMCP

from toolpipe.config import load_config, resolve_env_vars
from toolpipe.constants import INSTRUCTIONS
from toolpipe.errors import ConfigurationError
from toolpipe.models import ServerConfig, ToolPipeConfig
from toolpipe.results.manager import ResultManager
from toolpipe.server.consent import ConsentStore
from toolpipe.server.dispatcher import ToolDispatcher
from toolpipe.server.registry import ServerRegistry
from toolpipe.server.virtualization import ResultVirtualizationMiddleware
from toolpipe.tools import register_control_tools
from toolpipe.tools.pipe import register as register_pipe_tool
from toolpipe.tools.policy import register as register_policy_tools
from toolpipe.tools.servers import register as register_servers_tools

logger = logging.getLogger(__name__)


@dataclass
class ToolPipeApp:
    mcp: FastMCP
    registry: ServerRegistry
    manager: ResultManager
    dispatcher: ToolDispatcher
    consent: ConsentStore
    dynamic_servers: bool = True

    def close(self) -> None:
        self.manager.close()


async def create_app(
    config: ToolPipeConfig,
    discovered: list[ServerConfig] | None = None,
    dynamic_servers: bool = True,
) -> ToolPipeApp:
    mcp = FastMCP(name=config.name, instructions=INSTRUCTIONS)
    registry = ServerRegistry()
    for server in config.servers.values():
        registry.register(server)
    consent = ConsentStore(config.results.storage_dir)
    # Persisted approvals become approved servers (env resolved now;
    # unresolvable entries stay pending with a warning).
    for name, cfg in {**consent.always_entries(), **consent.workspace_entries()}.items():
        try:
            resolved = ServerConfig(
                name=cfg.name,
                command=cfg.command,
                args=list(cfg.args),
                url=cfg.url,
                env=resolve_env_vars(cfg.env),
            )
        except ConfigurationError as e:
            logger.warning("persisted server '%s' skipped: %s", name, e)
            if not registry.is_known(name):
                registry.register_known(cfg)
            continue
        if not registry.is_known(name):
            registry.register(resolved)
        else:
            registry.update_known(resolved)
            registry.approve(name)
    # Discovered entries are known but unapproved (consent on first use).
    for entry in discovered or []:
        if not registry.is_known(entry.name):
            registry.register_known(entry)
    registry.mount_approved(mcp)
    await registry.refresh_tool_map(mcp)
    manager = ResultManager.from_storage_dir(config.results.storage_dir, config.results)

    async def _consume_one_shot(owner: str) -> bool:
        if registry.consume_one_shot(owner):
            consent.revoke(owner)
            registry.unmount_server(mcp, owner)
            await registry.refresh_tool_map(mcp)
            return True
        return False

    def _policy_of(tool_name: str) -> str:
        server = registry.match_server(tool_name)
        if server is None:
            return "auto"
        tool = tool_name[len(server) + 1 :] if tool_name != server else None
        return consent.effective_policy(server, tool)

    register_control_tools(mcp, manager)
    mcp.add_middleware(
        ResultVirtualizationMiddleware(
            manager,
            config.results,
            is_virtualizable=registry.is_downstream_tool,
            owner_of=registry.owning_server,
            consume_one_shot=_consume_one_shot,
            map_updater=registry.update_map_from_tools,
            policy_of=_policy_of,
        )
    )
    dispatcher = ToolDispatcher(mcp)
    register_pipe_tool(mcp, manager, dispatcher, registry, consent)
    register_policy_tools(mcp, registry, consent)
    register_servers_tools(mcp, registry, consent, dynamic_servers)
    return ToolPipeApp(
        mcp=mcp,
        registry=registry,
        manager=manager,
        dispatcher=dispatcher,
        consent=consent,
        dynamic_servers=dynamic_servers,
    )


async def create_app_from_file(config_path: str) -> ToolPipeApp:
    return await create_app(load_config(config_path))
