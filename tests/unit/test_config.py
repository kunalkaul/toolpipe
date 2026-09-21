"""Config loader tests."""

import pytest

from toolpipe.config import load_config
from toolpipe.errors import ConfigurationError


def test_defaults_no_servers(tmp_path):
    cfg_file = tmp_path / "toolpipe.toml"
    cfg_file.write_text("")
    cfg = load_config(cfg_file)
    assert cfg.name == "ToolPipe"
    assert cfg.results.inline_max_bytes == 32768
    assert cfg.servers == {}


def test_servers_and_env_substitution(tmp_path, monkeypatch):
    monkeypatch.setenv("TP_TEST_TOKEN", "secret-123")
    cfg_file = tmp_path / "toolpipe.toml"
    cfg_file.write_text(
        """
[servers.db]
command = "uv"
args = ["run", "python", "x.py"]

[servers.db.env]
TOKEN = "${TP_TEST_TOKEN}"

[servers.remote]
url = "http://localhost:9001/mcp"
"""
    )
    cfg = load_config(cfg_file)
    assert cfg.servers["db"].command == "uv"
    assert cfg.servers["db"].env == {"TOKEN": "secret-123"}
    assert cfg.servers["remote"].url == "http://localhost:9001/mcp"


def test_missing_env_fails_fast(tmp_path):
    cfg_file = tmp_path / "toolpipe.toml"
    cfg_file.write_text(
        '[servers.gh]\ncommand = "npx"\n[servers.gh.env]\nT = "${TP_DEF_MISSING}"\n'
    )
    with pytest.raises(ConfigurationError, match="not set"):
        load_config(cfg_file)


def test_command_and_url_conflict(tmp_path):
    cfg_file = tmp_path / "toolpipe.toml"
    cfg_file.write_text('[servers.x]\ncommand = "uv"\nurl = "http://x"\n')
    with pytest.raises(ConfigurationError, match="both"):
        load_config(cfg_file)


def test_missing_file(tmp_path):
    with pytest.raises(ConfigurationError, match="not found"):
        load_config(tmp_path / "nope.toml")


def test_non_table_toolpipe_section(tmp_path):
    cfg_file = tmp_path / "toolpipe.toml"
    cfg_file.write_text('toolpipe = "foo"\n')
    with pytest.raises(ConfigurationError, match="must be a table"):
        load_config(cfg_file)


def test_unknown_results_key_rejected(tmp_path):
    cfg_file = tmp_path / "toolpipe.toml"
    cfg_file.write_text("[results]\ninline_max_byte = 100\n")
    with pytest.raises(ConfigurationError, match="Unknown"):
        load_config(cfg_file)


def test_negative_and_mistyped_settings_rejected(tmp_path):
    cfg_file = tmp_path / "toolpipe.toml"
    cfg_file.write_text("[results]\nttl_seconds = -5\n")
    with pytest.raises(ConfigurationError, match=">= 0"):
        load_config(cfg_file)
    cfg_file.write_text("[servers.x]\ncommand = 123\n")
    with pytest.raises(ConfigurationError, match="must be a string"):
        load_config(cfg_file)
