"""CLI smoke tests."""

import json
from datetime import timedelta

import pytest

import toolpipe.results.store as store_mod
from toolpipe.cli import build_parser, main
from toolpipe.models import ResultSettings
from toolpipe.results.manager import ResultManager
from toolpipe.utils.time import utc_now


def _config(tmp_path) -> str:
    cfg = tmp_path / "toolpipe.toml"
    cfg.write_text(
        f"""
[results]
storage_dir = "{tmp_path / ".toolpipe"}"
"""
    )
    return str(cfg)


def _seed(tmp_path) -> str:
    settings = ResultSettings(storage_dir=str(tmp_path / ".toolpipe"))
    manager = ResultManager.from_storage_dir(settings.storage_dir, settings)
    try:
        stored = manager.store(b'{"a": 1}', "application/json", "db_query")
        manager.incr_counter("proxied_tool_calls")
        manager.incr_counter("reference_response_bytes", 100)
        return stored.ref
    finally:
        manager.close()


def test_serve_parser_defaults():
    args = build_parser().parse_args(["serve"])
    assert args.config is None
    assert args.log_level == "WARNING"
    assert args.import_mcp_json is None
    assert args.import_codex_toml is None
    assert args.no_discover is False
    assert args.no_dynamic_servers is False


def test_inspect_json_output(tmp_path, capsys):
    cfg = _config(tmp_path)
    ref = _seed(tmp_path)
    main(["inspect", ref, "--config", cfg])
    out = json.loads(capsys.readouterr().out)
    assert out["ref"] == ref
    assert out["content_type"] == "application/json"
    assert out["source_tool"] == "db_query"


def test_inspect_missing_ref_exits_nonzero(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["inspect", "res_nope", "--config", _config(tmp_path)])
    assert exc.value.code == 1
    assert "not found" in capsys.readouterr().err


def test_results_table(tmp_path, capsys):
    cfg = _config(tmp_path)
    ref = _seed(tmp_path)
    main(["results", "--config", cfg])
    out = capsys.readouterr().out
    assert "REF" in out and ref in out and "application/json" in out


def test_stats_shows_avoided_bytes(tmp_path, capsys):
    cfg = _config(tmp_path)
    _seed(tmp_path)
    main(["stats", "--config", cfg])
    out = capsys.readouterr().out
    assert "Proxied tool calls:" in out
    assert "Estimated bytes avoided:" in out
    assert "Pipe calls:" in out


def test_stats_persist_across_restarts(tmp_path, capsys):
    cfg = _config(tmp_path)
    _seed(tmp_path)
    main(["stats", "--config", cfg])
    first = capsys.readouterr().out
    main(["stats", "--config", cfg])
    assert capsys.readouterr().out == first


def test_clean_removes_only_expired(tmp_path, capsys, monkeypatch):
    cfg = _config(tmp_path)
    ref = _seed(tmp_path)
    main(["clean", "--config", cfg])
    assert "Removed 0 expired result(s)." in capsys.readouterr().out
    # Travel past expiry, then clean.
    monkeypatch.setattr(store_mod, "utc_now", lambda: utc_now() + timedelta(hours=2))
    main(["clean", "--config", cfg])
    assert "Removed 1 expired result(s)." in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["inspect", ref, "--config", cfg])
