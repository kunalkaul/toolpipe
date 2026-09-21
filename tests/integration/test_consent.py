"""Consent + scopes integration tests over real STDIO."""

import json
import sys
from pathlib import Path

import pytest
from fastmcp import Client

from toolpipe.config import load_config
from toolpipe.models import ResultSettings, ServerConfig, ToolPipeConfig
from toolpipe.server.app import ToolPipeApp, create_app

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Never read/write the developer's real ~/.toolpipe in tests."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)


def _echo_config(name="echo") -> ServerConfig:
    return ServerConfig(
        name=name,
        command=sys.executable,
        args=[str(FIXTURES / "echo_a_mcp.py")],
    )


async def _make_app(tmp_path, discovered: list | None = None) -> ToolPipeApp:
    config = ToolPipeConfig(results=ResultSettings(storage_dir=str(tmp_path / ".toolpipe")))
    return await create_app(config, discovered=discovered or [])


async def test_pending_then_approve_workspace_then_call(tmp_path):
    app = await _make_app(tmp_path, [_echo_config()])
    try:
        async with Client(app.mcp) as client:
            names = {t.name for t in await client.list_tools()}
            assert "echo_echo" not in names
            pending = await client.call_tool("toolpipe_pending_servers", {})
            assert pending.structured_content == {
                "pending": [
                    {
                        "name": "echo",
                        "via": f"command `{sys.executable} {FIXTURES / 'echo_a_mcp.py'}`",
                    }
                ]
            }
            out = await client.call_tool(
                "toolpipe_approve_server", {"name": "echo", "scope": "workspace"}
            )
            assert out.structured_content == {
                "approved": True,
                "name": "echo",
                "scope": "workspace",
            }
            names = {t.name for t in await client.list_tools()}
            assert "echo_echo" in names
            r = await client.call_tool("echo_echo", {"text": "hi"})
            from mcp.types import TextContent

            assert isinstance(r.content[0], TextContent) and r.content[0].text == "A:hi"
        scope_file = tmp_path / ".toolpipe" / "servers.json"
        assert json.loads(scope_file.read_text())["servers"]["echo"]["command"] == sys.executable
    finally:
        app.close()


async def test_workspace_approval_survives_restart(tmp_path):
    app = await _make_app(tmp_path, [_echo_config()])
    try:
        async with Client(app.mcp) as client:
            await client.call_tool(
                "toolpipe_approve_server", {"name": "echo", "scope": "workspace"}
            )
    finally:
        app.close()
    app2 = await _make_app(tmp_path)
    try:
        async with Client(app2.mcp) as client:
            names = {t.name for t in await client.list_tools()}
            assert "echo_echo" in names
    finally:
        app2.close()


async def test_pipe_fallback_without_elicitation(tmp_path):
    app = await _make_app(tmp_path, [_echo_config()])
    try:
        async with Client(app.mcp) as client:
            ref = app.manager.store(b'{"v": 1}', "application/json", "t").ref
            with pytest.raises(Exception, match="toolpipe_approve_server"):
                await client.call_tool(
                    "toolpipe_pipe_result",
                    {"ref": ref, "target_tool": "echo_user_info", "mapping": {"name": "v"}},
                )
        scope_file = tmp_path / ".toolpipe" / "servers.json"
        assert not scope_file.exists()
    finally:
        app.close()


async def test_pipe_elicitation_accept(tmp_path):
    from fastmcp.client.elicitation import ElicitResult

    app = await _make_app(tmp_path, [_echo_config()])
    try:

        async def approve_workspace(message, response_type, params, ctx):
            return ElicitResult(action="accept", content={"value": "workspace"})

        async with Client(app.mcp, elicitation_handler=approve_workspace, mode="legacy") as client:
            ref = app.manager.store(b'{"name": "Zed"}', "application/json", "t").ref
            out = await client.call_tool(
                "toolpipe_pipe_result",
                {"ref": ref, "target_tool": "echo_user_info", "mapping": {"name": "name"}},
            )
            assert out.structured_content == {"name": "Zed", "id": 1}
        assert (tmp_path / ".toolpipe" / "servers.json").exists()
    finally:
        app.close()


async def test_pipe_elicitation_deny(tmp_path):
    from fastmcp.client.elicitation import ElicitResult

    app = await _make_app(tmp_path, [_echo_config()])
    try:

        async def deny(message, response_type, params, ctx):
            return ElicitResult(action="accept", content={"value": "deny"})

        async with Client(app.mcp, elicitation_handler=deny, mode="legacy") as client:
            ref = app.manager.store(b'{"v": 1}', "application/json", "t").ref
            with pytest.raises(Exception, match="not approved"):
                await client.call_tool(
                    "toolpipe_pipe_result",
                    {"ref": ref, "target_tool": "echo_user_info", "mapping": {"name": "v"}},
                )
        assert not (tmp_path / ".toolpipe" / "servers.json").exists()
    finally:
        app.close()


async def test_once_grant_consumed_after_first_call(tmp_path):
    app = await _make_app(tmp_path, [_echo_config()])
    try:
        async with Client(app.mcp) as client:
            await client.call_tool("toolpipe_approve_server", {"name": "echo", "scope": "once"})
            names = {t.name for t in await client.list_tools()}
            assert "echo_echo" in names
            r = await client.call_tool("echo_echo", {"text": "one"})
            from mcp.types import TextContent

            assert isinstance(r.content[0], TextContent)
            with pytest.raises(Exception, match="not found|No tool|Unknown tool"):
                await client.call_tool("echo_echo", {"text": "two"})
            names = {t.name for t in await client.list_tools()}
            assert "echo_echo" not in names
    finally:
        app.close()


async def test_session_grant_dies_with_restart(tmp_path):
    app = await _make_app(tmp_path, [_echo_config()])
    try:
        async with Client(app.mcp) as client:
            await client.call_tool("toolpipe_approve_server", {"name": "echo", "scope": "session"})
            names = {t.name for t in await client.list_tools()}
            assert "echo_echo" in names
    finally:
        app.close()
    app2 = await _make_app(tmp_path, [_echo_config()])
    try:
        async with Client(app2.mcp) as client:
            pending = await client.call_tool("toolpipe_pending_servers", {})
            assert pending.structured_content is not None
            assert [p["name"] for p in pending.structured_content["pending"]] == ["echo"]
    finally:
        app2.close()


async def test_remove_unmounts_but_keeps_results(tmp_path):
    app = await _make_app(tmp_path, [_echo_config()])
    try:
        async with Client(app.mcp) as client:
            await client.call_tool("toolpipe_approve_server", {"name": "echo", "scope": "session"})
            ref = app.manager.store(b'{"v": 1}', "application/json", "echo_echo").ref
            out = await client.call_tool("toolpipe_remove_server", {"name": "echo"})
            assert out.structured_content == {"removed": True, "name": "echo"}
            names = {t.name for t in await client.list_tools()}
            assert "echo_echo" not in names
            info = await client.call_tool("toolpipe_inspect_result", {"ref": ref})
            assert info.structured_content is not None
            assert info.structured_content["ref"] == ref
    finally:
        app.close()


async def test_unknown_server_pipe_points_to_add(tmp_path):
    app = await _make_app(tmp_path)
    try:
        async with Client(app.mcp) as client:
            ref = app.manager.store(b'{"v": 1}', "application/json", "t").ref
            with pytest.raises(Exception, match="toolpipe_add_server"):
                await client.call_tool(
                    "toolpipe_pipe_result",
                    {"ref": ref, "target_tool": "ghost_tool", "mapping": {}},
                )
    finally:
        app.close()


async def test_add_server_manual_then_call(tmp_path):
    app = await _make_app(tmp_path)
    try:
        async with Client(app.mcp) as client:
            out = await client.call_tool(
                "toolpipe_add_server",
                {
                    "name": "manual",
                    "scope": "session",
                    "command": sys.executable,
                    "args": [str(FIXTURES / "echo_b_mcp.py")],
                },
            )
            assert out.structured_content is not None
            assert out.structured_content["approved"] is True
            r = await client.call_tool("manual_echo", {"text": "yo"})
            from mcp.types import TextContent

            assert isinstance(r.content[0], TextContent) and r.content[0].text == "B:yo"
            with pytest.raises(Exception, match="already registered"):
                await client.call_tool(
                    "toolpipe_add_server",
                    {"name": "manual", "command": "x"},
                )
    finally:
        app.close()


async def test_lazy_spawn_only_on_first_call(tmp_path, monkeypatch):
    marker = tmp_path / "started"
    monkeypatch.setenv("TP_MARKER_FILE", str(marker))
    cfg = ServerConfig(
        name="marker",
        command=sys.executable,
        args=[str(FIXTURES / "marker_mcp.py")],
        env={"TP_MARKER_FILE": "${TP_MARKER_FILE}"},
    )
    app = await _make_app(tmp_path, [cfg])
    try:
        async with Client(app.mcp) as client:
            names = {t.name for t in await client.list_tools()}
            assert "marker_ping" not in names
            assert not marker.exists()
            await client.call_tool(
                "toolpipe_approve_server", {"name": "marker", "scope": "workspace"}
            )
            # Approval mounts without enumerating; the client's list_changed
            # re-list is the first actual use and spawns the server.
            r = await client.call_tool("marker_ping", {})
            from mcp.types import TextContent

            assert isinstance(r.content[0], TextContent) and r.content[0].text == "pong"
            assert marker.exists()
    finally:
        app.close()


async def test_no_dynamic_servers_hides_mutations(tmp_path):
    config = ToolPipeConfig(results=ResultSettings(storage_dir=str(tmp_path / ".toolpipe")))
    app = await create_app(config, discovered=[_echo_config()], dynamic_servers=False)
    try:
        async with Client(app.mcp) as client:
            names = {t.name for t in await client.list_tools()}
            assert "toolpipe_pending_servers" in names
            assert "toolpipe_list_servers" in names
            assert "toolpipe_add_server" not in names
            assert "toolpipe_approve_server" not in names
            assert "toolpipe_remove_server" not in names
    finally:
        app.close()


async def test_list_servers_redacts_secrets(tmp_path):
    cfg = _echo_config()
    cfg = ServerConfig(name=cfg.name, command=cfg.command, args=cfg.args, env={"T": "secret"})
    app = await _make_app(tmp_path, [cfg])
    try:
        async with Client(app.mcp) as client:
            await client.call_tool("toolpipe_approve_server", {"name": "echo", "scope": "session"})
            listed = await client.call_tool("toolpipe_list_servers", {})
            assert listed.structured_content is not None
            entry = listed.structured_content["servers"][0]
            assert entry["env"] == {"T": "<set>"}
            assert "secret" not in json.dumps(listed.structured_content)
    finally:
        app.close()


def test_load_config_untouched_by_consent(tmp_path):
    cfg_file = tmp_path / "toolpipe.toml"
    cfg_file.write_text('[servers.db]\ncommand = "uv"\n')
    assert load_config(cfg_file).servers["db"].command == "uv"
