"""ToolPipe CLI: serve + local result debugging."""

import argparse
import asyncio
import contextlib
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from toolpipe.config import load_config
from toolpipe.errors import ConfigurationError, ToolPipeError
from toolpipe.models import ToolPipeConfig
from toolpipe.results.cleanup import periodic_cleanup, run_startup_cleanup
from toolpipe.results.manager import ResultManager
from toolpipe.server.app import create_app
from toolpipe.utils.sizes import format_bytes
from toolpipe.utils.time import parse_iso, utc_now


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="toolpipe", description="Pass MCP results by reference, not through the LLM."
    )
    sub = p.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="Run ToolPipe MCP server (STDIO).")
    serve.add_argument("--config", default=None)
    serve.add_argument("--log-level", default="WARNING")
    serve.add_argument("--import-mcp-json", default=None)
    serve.add_argument("--import-codex-toml", default=None)
    serve.add_argument("--no-discover", action="store_true")
    serve.add_argument("--no-dynamic-servers", action="store_true")

    inspect_p = sub.add_parser("inspect", help="Show a stored result (local debugging).")
    inspect_p.add_argument("ref")
    inspect_p.add_argument("--config", default="toolpipe.toml")

    results_p = sub.add_parser("results", help="List stored results.")
    results_p.add_argument("--config", default="toolpipe.toml")

    stats_p = sub.add_parser("stats", help="Show result store metrics.")
    stats_p.add_argument("--config", default="toolpipe.toml")

    clean_p = sub.add_parser("clean", help="Remove expired results.")
    clean_p.add_argument("--config", default="toolpipe.toml")
    return p


def _configure_logging(level: str) -> None:
    logging.basicConfig(stream=sys.stderr, level=getattr(logging, level.upper(), logging.WARNING))


def _open_manager(config_path: str) -> tuple[ToolPipeConfig, ResultManager]:
    try:
        config = load_config(config_path)
    except ConfigurationError as e:
        print(f"toolpipe: configuration error: {e}", file=sys.stderr)
        raise SystemExit(2)
    return config, ResultManager.from_storage_dir(config.results.storage_dir, config.results)


def _load_serve_config(args: argparse.Namespace) -> tuple[ToolPipeConfig, list]:
    """Build the serve config.

    Returns (config, discovered): TOML + explicit imports are pre-approved;
    auto-discovered entries stay pending until first-use consent.
    """
    from toolpipe import discovery
    from toolpipe.config import merge_server_maps, servers_from_discovered
    from toolpipe.models import ServerConfig

    if args.config is not None:
        base = load_config(args.config)
    elif Path("toolpipe.toml").exists():
        base = load_config("toolpipe.toml")
    else:
        base = ToolPipeConfig()

    imported: dict = {}
    if args.import_mcp_json is not None:
        try:
            text = Path(args.import_mcp_json).read_text()
        except OSError as e:
            raise ConfigurationError(f"Cannot read {args.import_mcp_json}: {e}")
        try:
            entries = discovery.parse_mcp_json(text, args.import_mcp_json)
        except ValueError as e:
            raise ConfigurationError(f"Invalid MCP JSON {args.import_mcp_json}: {e}")
        imported.update(servers_from_discovered(entries, strict=True, source_label="import"))
    if args.import_codex_toml is not None:
        try:
            text = Path(args.import_codex_toml).read_text()
        except OSError as e:
            raise ConfigurationError(f"Cannot read {args.import_codex_toml}: {e}")
        try:
            entries = discovery.parse_codex_toml(text, args.import_codex_toml)
        except ValueError as e:
            raise ConfigurationError(f"Invalid Codex TOML {args.import_codex_toml}: {e}")
        imported.update(servers_from_discovered(entries, strict=True, source_label="import"))

    discovered: dict = {}
    if not args.no_discover:
        discovered = servers_from_discovered(
            discovery.discover("."), strict=False, source_label="discovery"
        )

    # Precedence: TOML < explicit imports. Discovered entries are returned
    # separately as pending (consent on first use), not merged as active.
    # Persisted scope files layer around this chain (see ConsentStore).
    servers = merge_server_maps(base.servers, imported)
    pending = [
        ServerConfig(name=name, command=cfg.command, args=cfg.args, url=cfg.url, env=cfg.env)
        for name, cfg in discovered.items()
        if name not in servers
    ]
    return (
        ToolPipeConfig(name=base.name, results=base.results, servers=servers),
        pending,
    )


def _rel(iso: str, now: datetime) -> str:
    seconds = max(0, int((now - parse_iso(iso)).total_seconds()))
    return _fmt_duration(seconds)


def _rel_remaining(iso: str, now: datetime) -> str:
    seconds = max(0, int((parse_iso(iso) - now).total_seconds()))
    return _fmt_duration(seconds)


def _fmt_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _serve(args: argparse.Namespace) -> None:
    _configure_logging(args.log_level)
    try:
        config, discovered = _load_serve_config(args)

        async def _run() -> None:
            app = await create_app(
                config, discovered=discovered, dynamic_servers=not args.no_dynamic_servers
            )
            if not config.servers:
                print(
                    "toolpipe: no downstream servers configured — "
                    "only control tools are exposed. Add servers via "
                    "toolpipe_add_server, --config, or --import-mcp-json.",
                    file=sys.stderr,
                )
            run_startup_cleanup(app.manager)
            cleanup_task = asyncio.create_task(periodic_cleanup(app.manager))
            try:
                # STDIO is the protocol channel; logs go to stderr only.
                await app.mcp.run_stdio_async()
            finally:
                cleanup_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await cleanup_task
                app.close()

        asyncio.run(_run())
    except ConfigurationError as e:
        print(f"toolpipe: configuration error: {e}", file=sys.stderr)
        raise SystemExit(2)


def _inspect(config_path: str, ref: str) -> None:
    _, manager = _open_manager(config_path)
    try:
        info = manager.inspect(ref)
    except ToolPipeError as e:
        print(f"toolpipe: {e}", file=sys.stderr)
        raise SystemExit(1)
    finally:
        manager.close()
    print(
        json.dumps(
            {
                "ref": info.ref,
                "content_type": info.content_type,
                "size_bytes": info.size_bytes,
                "source_tool": info.source_tool,
                "created_at": info.created_at,
                "expires_at": info.expires_at,
                "preview": json.loads(info.preview_json) if info.preview_json else None,
            },
            indent=2,
        )
    )


def _results(config_path: str) -> None:
    _, manager = _open_manager(config_path)
    try:
        rows = manager.list_results()
    finally:
        manager.close()
    now = utc_now()
    print(f"{'REF':<24} {'TYPE':<18} {'SIZE':<10} {'AGE':<6} {'EXPIRES'}")
    for row in rows:
        print(
            f"{row.ref:<24} {row.content_type:<18} {format_bytes(row.size_bytes):<10} "
            f"{_rel(row.created_at, now):<6} {_rel_remaining(row.expires_at, now)}"
        )


def _stats(config_path: str) -> None:
    _, manager = _open_manager(config_path)
    try:
        stats = manager.stats()
    finally:
        manager.close()
    print(f"Proxied tool calls:          {stats['proxied_tool_calls']}")
    print(f"Virtualized results:         {stats['virtualized_results']}")
    print(f"Bytes virtualized:           {format_bytes(stats['virtualized_bytes'])}")
    print(f"Bytes returned as refs:      {format_bytes(stats['reference_response_bytes'])}")
    print(f"Estimated bytes avoided:     {format_bytes(stats['estimated_bytes_avoided'])}")
    print(f"Pipe calls:                  {stats['pipe_calls']}")
    print(f"Stored results:              {stats['stored_results']}")
    print(f"Current storage:             {format_bytes(stats['current_storage_bytes'])}")


def _clean(config_path: str) -> None:
    _, manager = _open_manager(config_path)
    try:
        count = manager.cleanup_expired()
    finally:
        manager.close()
    print(f"Removed {count} expired result(s).")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command is None:
        build_parser().print_help()
    elif args.command == "serve":
        _serve(args)
    elif args.command == "inspect":
        _inspect(args.config, args.ref)
    elif args.command == "results":
        _results(args.config)
    elif args.command == "stats":
        _stats(args.config)
    elif args.command == "clean":
        _clean(args.config)


if __name__ == "__main__":
    main()
