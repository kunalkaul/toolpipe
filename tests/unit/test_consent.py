"""Consent scope file tests."""

import json

import pytest

from toolpipe.errors import ConfigurationError
from toolpipe.models import ServerConfig
from toolpipe.server.consent import (
    ConsentStore,
    ScopeData,
    always_scope_path,
    load_scope_file,
    workspace_scope_path,
)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Policy/grant tests must not see the developer's real ~/.toolpipe."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)


def _cfg(name="s", **overrides):
    kwargs: dict = {"name": name, "command": "cmd"}
    kwargs.update(overrides)
    return ServerConfig(**kwargs)


def test_workspace_grant_persists_and_reloads(tmp_path):
    store = ConsentStore(tmp_path / ".toolpipe")
    store.grant(_cfg("db"), "workspace")
    path = workspace_scope_path(tmp_path / ".toolpipe")
    assert json.loads(path.read_text())["servers"]["db"]["command"] == "cmd"
    reloaded = ConsentStore(tmp_path / ".toolpipe")
    assert reloaded.grant_scope("db") == "workspace"


def test_once_and_session_are_memory_only(tmp_path):
    store = ConsentStore(tmp_path / ".toolpipe")
    store.grant(_cfg("a"), "once")
    store.grant(_cfg("b"), "session")
    assert store.consume_once("a") is True
    assert store.consume_once("a") is False
    assert not (tmp_path / ".toolpipe" / "servers.json").exists()
    assert ConsentStore(tmp_path / ".toolpipe").grant_scope("b") is None


def test_revoke_clears_every_scope(tmp_path):
    store = ConsentStore(tmp_path / ".toolpipe")
    store.grant(_cfg("s"), "session")
    store.grant(_cfg("s"), "always")
    assert store.revoke("s") is True
    assert store.grant_scope("s") is None
    assert store.revoke("missing") is False


def test_bad_scope_and_bad_files(tmp_path):
    store = ConsentStore(tmp_path / ".toolpipe")
    with pytest.raises(ConfigurationError, match="Scope must be"):
        store.grant(_cfg("x"), "forever")
    bad = tmp_path / "bad.json"
    bad.write_text('{"servers": {"ok": {"command": "c"}, "bad": {"nope": 1}}}')
    loaded = load_scope_file(bad)
    assert list(loaded.servers) == ["ok"]
    bad.write_text("not json")
    assert load_scope_file(bad) == ScopeData()


def test_always_path_is_user_level(tmp_path):
    store = ConsentStore(tmp_path / ".toolpipe")
    store.grant(_cfg("g"), "always")
    home = tmp_path / "home"
    assert (home / ".toolpipe" / "servers.json").exists()
    assert always_scope_path().parent == home / ".toolpipe"


def test_policy_resolution_and_persistence(tmp_path):
    store = ConsentStore(tmp_path / ".toolpipe")
    assert store.effective_policy("db", "q") == "auto"
    store.set_policy("db", "never", "always")
    store.set_policy("db:big", "always", "workspace")
    assert store.effective_policy("db", "small") == "never"
    assert store.effective_policy("db", "big") == "always"
    assert store.effective_policy("other", None) == "auto"
    reloaded = ConsentStore(tmp_path / ".toolpipe")
    assert reloaded.effective_policy("db", "big") == "always"
    policies = {p["target"]: p for p in reloaded.all_policies()}
    assert policies["db"]["scope"] == "always"
    assert policies["db:big"]["scope"] == "workspace"


def test_policy_bad_mode_and_bad_file(tmp_path):
    store = ConsentStore(tmp_path / ".toolpipe")
    with pytest.raises(ConfigurationError, match="Mode must be"):
        store.set_policy("db", "sometimes", "workspace")
    bad = tmp_path / "bad.json"
    bad.write_text('{"servers": {}, "policies": {"db": "never", "x": "bogus"}}')
    loaded = load_scope_file(bad)
    assert loaded.policies == {"db": "never"}


def test_revoke_drops_server_policies(tmp_path):
    store = ConsentStore(tmp_path / ".toolpipe")
    store.grant(_cfg("db"), "workspace")
    store.set_policy("db", "never", "workspace")
    store.set_policy("db:big", "always", "workspace")
    store.set_policy("other", "never", "workspace")
    assert store.revoke("db") is True
    assert store.effective_policy("db", "big") == "auto"
    assert store.effective_policy("other", None) == "never"
