"""Virtualization policy tests over real STDIO."""

import sys
from pathlib import Path

import pytest
from fastmcp import Client

from toolpipe.models import ResultSettings, ToolPipeConfig
from toolpipe.server.app import ToolPipeApp, create_app

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Never read/write the developer's real ~/.toolpipe in tests."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)


async def _make_app(tmp_path) -> ToolPipeApp:
    from toolpipe.models import ServerConfig

    config = ToolPipeConfig(results=ResultSettings(storage_dir=str(tmp_path / ".toolpipe")))
    discovered = [
        ServerConfig(
            name="echo",
            command=sys.executable,
            args=[str(FIXTURES / "echo_a_mcp.py")],
        )
    ]
    return await create_app(config, discovered=discovered)


async def test_never_passes_huge_result_through(tmp_path):
    app = await _make_app(tmp_path)
    try:
        async with Client(app.mcp) as client:
            await client.call_tool("toolpipe_approve_server", {"name": "echo", "scope": "session"})
            await client.call_tool("toolpipe_set_policy", {"target": "echo", "mode": "never"})
            out = await client.call_tool("echo_big_payload", {"size_kb": 64})
            assert out.structured_content is not None
            assert "toolpipe" not in out.structured_content
            assert len(out.structured_content["blob"]) == 64 * 1024
            assert app.manager.stats()["stored_results"] == 0
            # ...but the call still counts.
            assert app.manager.stats()["proxied_tool_calls"] >= 1
    finally:
        app.close()


async def test_always_virtualizes_tiny_result(tmp_path):
    app = await _make_app(tmp_path)
    try:
        async with Client(app.mcp) as client:
            await client.call_tool("toolpipe_approve_server", {"name": "echo", "scope": "session"})
            await client.call_tool(
                "toolpipe_set_policy", {"target": "echo:user_info", "mode": "always"}
            )
            out = await client.call_tool("echo_user_info", {"name": "Ada"})
            assert out.structured_content is not None
            assert out.structured_content["toolpipe"]["virtualized"] is True
            # Sibling tool keeps default auto behavior.
            small = await client.call_tool("echo_echo", {"text": "hi"})
            assert small.structured_content is None or "toolpipe" not in (
                small.structured_content or {}
            )
    finally:
        app.close()


async def test_policy_survives_restart(tmp_path):
    app = await _make_app(tmp_path)
    try:
        async with Client(app.mcp) as client:
            await client.call_tool(
                "toolpipe_approve_server", {"name": "echo", "scope": "workspace"}
            )
            await client.call_tool("toolpipe_set_policy", {"target": "echo", "mode": "never"})
    finally:
        app.close()
    app2 = await _make_app(tmp_path)
    try:
        assert app2.consent.effective_policy("echo", "big_payload") == "never"
        async with Client(app2.mcp) as client:
            got = await client.call_tool("toolpipe_get_policy", {})
            assert got.structured_content is not None
            assert {
                "target": "echo",
                "mode": "never",
                "scope": "workspace",
            } in got.structured_content["policies"]
    finally:
        app2.close()


async def test_unknown_policy_target_rejected(tmp_path):
    app = await _make_app(tmp_path)
    try:
        async with Client(app.mcp) as client:
            with pytest.raises(Exception, match="Unknown server"):
                await client.call_tool("toolpipe_set_policy", {"target": "ghost", "mode": "never"})
            with pytest.raises(Exception, match="Mode must be"):
                await client.call_tool(
                    "toolpipe_set_policy", {"target": "echo", "mode": "sometimes"}
                )
            # Enumerate first so the tool check is authoritative.
            await client.call_tool("toolpipe_approve_server", {"name": "echo", "scope": "session"})
            await client.list_tools()
            with pytest.raises(Exception, match="Unknown tool"):
                await client.call_tool(
                    "toolpipe_set_policy", {"target": "echo:nope", "mode": "never"}
                )
            out = await client.call_tool(
                "toolpipe_set_policy", {"target": "echo:echo", "mode": "never"}
            )
            assert out.structured_content is not None
            assert out.structured_content["mode"] == "never"
    finally:
        app.close()
