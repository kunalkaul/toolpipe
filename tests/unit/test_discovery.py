"""IDE discovery adapter tests."""

import json

import pytest

from toolpipe import discovery
from toolpipe.config import merge_server_maps, servers_from_discovered
from toolpipe.errors import ConfigurationError
from toolpipe.models import ServerConfig


def test_mcp_servers_shape_stdio_and_remote():
    entries = discovery.parse_mcp_json(
        json.dumps(
            {
                "mcpServers": {
                    "gh": {
                        "command": "npx",
                        "args": ["-y", "x"],
                        "env": {"T": "v"},
                    },
                    "remote": {"url": "http://h/mcp"},
                }
            }
        ),
        "test",
    )
    by_name = {e.name: e for e in entries}
    assert by_name["gh"].command == "npx"
    assert by_name["gh"].args == ["-y", "x"]
    assert by_name["remote"].url == "http://h/mcp"


def test_mcp_servers_shape_rejects_bad_entries():
    with pytest.raises(ValueError, match="needs 'command' or 'url'"):
        discovery.parse_mcp_json(json.dumps({"mcpServers": {"x": {}}}), "test")
    with pytest.raises(ValueError, match="cannot set both"):
        discovery.parse_mcp_json(
            json.dumps({"mcpServers": {"x": {"command": "c", "url": "u"}}}), "test"
        )


def test_opencode_array_command_and_environment():
    entries = discovery.parse_opencode(
        json.dumps(
            {
                "mcp": {
                    "local": {
                        "type": "local",
                        "command": ["bun", "x", "cmd"],
                        "environment": {"K": "V"},
                    },
                    "off": {"type": "local", "command": ["c"], "enabled": False},
                    "rem": {"type": "remote", "url": "https://h/mcp"},
                }
            }
        ),
        "test",
    )
    by_name = {e.name: e for e in entries}
    assert by_name["local"].command == "bun"
    assert by_name["local"].args == ["x", "cmd"]
    assert by_name["local"].env == {"K": "V"}
    assert "off" not in by_name
    assert by_name["rem"].url == "https://h/mcp"


def test_opencode_nested_servers_key():
    entries = discovery.parse_opencode(
        json.dumps({"mcp": {"servers": {"a": {"command": ["c"]}}}}), "test"
    )
    assert entries[0].name == "a"


def test_zed_context_servers():
    entries = discovery.parse_zed(
        json.dumps(
            {
                "context_servers": {
                    "local": {"command": "cmd", "args": ["a"], "env": {}},
                    "remote": {"url": "https://h/mcp"},
                }
            }
        ),
        "test",
    )
    by_name = {e.name: e for e in entries}
    assert by_name["local"].command == "cmd"
    assert by_name["remote"].url == "https://h/mcp"


def test_codex_toml_and_enabled_flag():
    entries = discovery.parse_codex_toml(
        """
[mcp_servers.a]
command = "npx"
args = ["-y", "x"]
env = { T = "v" }

[mcp_servers.b]
url = "https://h/mcp"

[mcp_servers.c]
command = "x"
enabled = false
""",
        "test",
    )
    by_name = {e.name: e for e in entries}
    assert by_name["a"].args == ["-y", "x"]
    assert by_name["b"].url == "https://h/mcp"
    assert "c" not in by_name


def test_jsonc_comments_stripped():
    entries = discovery.parse_mcp_json(
        '{\n// comment\n"mcpServers": {\n/* multi\nline */\n"x": {"command": "c"}\n}\n}',
        "test",
    )
    assert entries[0].name == "x"
    # URLs containing // survive stripping.
    entries = discovery.parse_mcp_json('{"mcpServers": {"r": {"url": "https://h/mcp"}}}', "test")
    assert entries[0].url == "https://h/mcp"


def test_env_substitution_strict_vs_lenient(monkeypatch):
    from toolpipe.discovery import DiscoveredServer

    entries = [DiscoveredServer(name="s", command="c", env={"T": "${TP_MISSING_XYZ}"})]
    with pytest.raises(ConfigurationError):
        servers_from_discovered(entries, strict=True)
    monkeypatch.setenv("TP_MISSING_XYZ", "v")
    servers = servers_from_discovered(entries, strict=True)
    assert servers["s"].env == {"T": "v"}


def test_lenient_mode_skips_bad_entries():
    from toolpipe.discovery import DiscoveredServer

    entries = [
        DiscoveredServer(name="good", command="c"),
        DiscoveredServer(name="bad", command=None, url=None),
    ]
    servers = servers_from_discovered(entries, strict=False)
    assert list(servers) == ["good"]


def test_merge_later_wins():
    base = {"s": ServerConfig(name="s", command="old")}
    over = {"s": ServerConfig(name="s", command="new")}
    assert merge_server_maps(base, over)["s"].command == "new"


def test_discover_workspace_overrides_user(tmp_path, monkeypatch):
    home = tmp_path / "home"
    ws = tmp_path / "ws"
    (ws / ".cursor").mkdir(parents=True)
    (ws / ".cursor" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"s": {"command": "workspace-cmd"}}})
    )
    home.mkdir()
    (home / ".cursor").mkdir()
    (home / ".cursor" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"s": {"command": "user-cmd"}}})
    )
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    servers = servers_from_discovered(discovery.discover(ws), strict=False)
    assert servers["s"].command == "workspace-cmd"


def test_discover_bad_file_skipped_not_fatal(tmp_path, monkeypatch):
    home = tmp_path / "home2"
    home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    ws = tmp_path / "ws2"
    ws.mkdir()
    (ws / ".mcp.json").write_text("not json{{{")
    assert discovery.discover(ws) == []
