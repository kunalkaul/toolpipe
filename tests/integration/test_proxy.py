"""Proxy shell tests: namespacing, call-through, error propagation, schema normalization."""

import sys
from pathlib import Path

import pytest
from fastmcp import Client
from mcp.types import TextContent

from toolpipe.config import load_config
from toolpipe.server.app import ToolPipeApp, create_app
from toolpipe.server.schema_transform import VIRTUALIZATION_NOTE

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _config_text(tmp_path) -> str:
    a = FIXTURES / "echo_a_mcp.py"
    b = FIXTURES / "echo_b_mcp.py"
    return f"""
[results]
storage_dir = "{tmp_path / ".toolpipe"}"

[servers.echo_a]
command = "{sys.executable}"
args = ["{a}"]

[servers.echo_b]
command = "{sys.executable}"
args = ["{b}"]
"""


async def _make_app(tmp_path) -> ToolPipeApp:
    cfg_file = tmp_path / "toolpipe.toml"
    cfg_file.write_text(_config_text(tmp_path))
    return await create_app(load_config(cfg_file))


async def test_colliding_tools_are_namespaced(tmp_path):
    app = await _make_app(tmp_path)
    try:
        async with Client(app.mcp) as client:
            names = {t.name for t in await client.list_tools()}
        assert "echo_a_echo" in names
        assert "echo_b_echo" in names
    finally:
        app.close()


async def test_call_through_and_error_propagation(tmp_path):
    app = await _make_app(tmp_path)
    try:
        async with Client(app.mcp) as client:
            r1 = await client.call_tool("echo_a_echo", {"text": "hi"})
            r2 = await client.call_tool("echo_b_echo", {"text": "hi"})
            assert isinstance(r1.content[0], TextContent) and r1.content[0].text == "A:hi"
            assert isinstance(r2.content[0], TextContent) and r2.content[0].text == "B:hi"
            assert app.registry.is_downstream_tool("echo_a_echo")
            assert app.registry.owning_server("echo_b_echo") == "echo_b"
            with pytest.raises(Exception, match="A failed"):
                await client.call_tool("echo_a_boom", {})
    finally:
        app.close()


async def test_exposed_output_schema_removed_but_tool_still_executes(tmp_path):
    app = await _make_app(tmp_path)
    try:
        async with Client(app.mcp) as client:
            tools = {t.name: t for t in await client.list_tools()}
            user_tool = tools["echo_a_user_info"]
            # Input schema preserved, output schema stripped.
            assert "name" in user_tool.input_schema["properties"]
            assert user_tool.output_schema is None
            assert VIRTUALIZATION_NOTE in (user_tool.description or "")
            # Small structured result still executes and stays usable.
            result = await client.call_tool("echo_a_user_info", {"name": "Ada"})
            assert result.structured_content == {"name": "Ada", "id": 1}
    finally:
        app.close()
