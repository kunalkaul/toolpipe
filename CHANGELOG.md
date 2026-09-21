# Changelog

## v0.1.0

Initial release: ToolPipe MCP data-plane proxy.

- STDIO proxying of multiple downstream MCP servers with namespaced tools;
  overlapping namespaces resolve by longest prefix.
- Fail-fast TOML config validation (bad sections, unknown keys, bad
  ranges/types) with `${VAR}` environment substitution.
- Output-schema normalization so large responses can become references.
- SQLite + content-hashed blob result store (TTL, refcounts, dedup-aware
  storage limits, blob repair, ref-collision retry).
- Automatic virtualization middleware (JSON + text, compact `res_*` envelopes);
  limit rejections raise a clear `ToolError`.
- Control tools: inspect, read, select (JMESPath), search (iterative walk with
  depth cap and exact truncation), release, pipe.
- Zero-config serve with IDE auto-discovery (Claude Code, Cursor, Codex,
  OpenCode, Zed); first-use consent (once/session/workspace/always) with
  elicitation and approve-tool fallback; scope files with unresolved `${VAR}`.
- Server management tools (pending/approve/add/remove/list) and per-server/tool
  virtualization policies (auto/always/never).
- Direct result piping with literal args, collision detection, single-bump
  reads, and re-virtualization of large target outputs.
- CLI: serve, inspect, results, stats, clean; stderr-only logging.
- Demo producer/analytics pair; 100k-customer acceptance test.
