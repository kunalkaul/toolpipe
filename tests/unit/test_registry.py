"""Registry validation tests."""

import pytest
from fastmcp import FastMCP

from toolpipe.errors import ConfigurationError
from toolpipe.models import ServerConfig
from toolpipe.server.registry import ServerRegistry


def test_duplicate_server_name_rejected():
    reg = ServerRegistry()
    reg.register(ServerConfig(name="db", command="uv"))
    with pytest.raises(ConfigurationError, match="Duplicate"):
        reg.register(ServerConfig(name="db", command="uv"))


def test_reserved_name_rejected():
    reg = ServerRegistry()
    with pytest.raises(ConfigurationError, match="reserved"):
        reg.register(ServerConfig(name="toolpipe", command="uv"))


def test_unknown_tool_is_not_downstream():
    reg = ServerRegistry()
    assert not reg.is_downstream_tool("toolpipe_pipe_result")
    assert reg.owning_server("nope") is None


async def test_overlapping_namespaces_attribute_longest_prefix():
    reg = ServerRegistry()
    reg.register(ServerConfig(name="prod", command="x"))
    reg.register(ServerConfig(name="prod_extra", command="x"))
    root = FastMCP("root")

    @root.tool(name="prod_extra_foo")
    def foo() -> str:
        return "x"

    await reg.refresh_tool_map(root)
    assert reg.owning_server("prod_extra_foo") == "prod_extra"
