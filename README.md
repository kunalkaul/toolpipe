# ToolPipe

> **Pass MCP results by reference, not through the LLM.**

ToolPipe is an MCP data-plane proxy that keeps large tool outputs out of LLM context by storing them as references and piping the underlying data directly between tools.

```text
Without ToolPipe:  Tool A → 18 MB → LLM → Tool B
With ToolPipe:     Tool A → ToolPipe storage ├→ LLM: res_xxx └→ Tool B: actual data
```

The LLM stays the **reasoning plane** (it sees a small `res_*` reference plus a
preview). ToolPipe is the **data plane** (it holds the bytes and feeds them to
the next tool).

## How it works

1. ToolPipe exposes downstream MCP servers' tools through one server, namespaced
   (`producer_generate_customers`, `analytics_calculate_statistics`).
2. When a downstream tool returns more than `inline_max_bytes` (default 32 KiB),
   ToolPipe stores the payload locally (SQLite metadata + content-hashed blobs)
   and returns a compact reference envelope instead.
3. The agent then uses control tools to inspect, read, search, or select from
   the stored result — or pipes data straight into another tool without the
   payload ever re-entering context.

```text
100,000 customer objects (~5.1 MiB)
        │ producer_generate_customers
        ▼
ToolPipe res_A (542 B: ref + preview)
        │ mapping customers[].revenue
        ▼ analytics_calculate_statistics
{count: 100000, min: 0.0, max: 4999.0, mean: 2499.5} (60 B)
```

Measured on the bundled demo: **5,355,595 B → 542 B (~9881x smaller)** for the
producer step; the final stats response is 60 B.

## Installation

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run toolpipe --help
```

## Configuration

Zero-config first: just add ToolPipe to your agent — no `toolpipe.toml`
needed.

```bash
toolpipe serve
```

On startup ToolPipe auto-discovers the MCP servers you already configured in
Claude Code (`.mcp.json`), Cursor (`.cursor/mcp.json`), Codex
(`.codex/config.toml`), OpenCode (`opencode.json`), and Zed
(`.zed/settings.json`), workspace files first. Discovered servers spawn
nothing until you approve them: the first time the agent routes a call through
one, **you** are asked — once, for this session, for this workspace, or always
— and your choice is saved. Static TOML and explicit imports work as before:

```bash
toolpipe serve --config toolpipe.toml
toolpipe serve --import-mcp-json path/to/mcp.json
toolpipe serve --import-codex-toml ~/.codex/config.toml
toolpipe serve --no-discover --no-dynamic-servers
```

See `examples/toolpipe.toml` (working demo config). Format:

```toml
[toolpipe]
name = "ToolPipe"

[results]
storage_dir = ".toolpipe"
inline_max_bytes = 32768   # below this: pass through unchanged
preview_max_bytes = 4096   # preview budget inside reference envelopes
read_max_bytes = 16384     # max bytes per toolpipe_read_result call
max_result_bytes = 104857600
max_store_bytes = 1073741824
ttl_seconds = 3600

[servers.producer]
command = "uv"
args = ["run", "python", "examples/demo/producer_mcp.py"]

[servers.analytics]
command = "uv"
args = ["run", "python", "examples/demo/analytics_mcp.py"]

[servers.remote]
url = "http://localhost:9001/mcp"
```

Secrets use `${VAR}` substitution and fail fast when unset:

```toml
[servers.github]
command = "npx"
args = ["-y", "@example/github-mcp"]

[servers.github.env]
GITHUB_TOKEN = "${GITHUB_TOKEN}"
```

### MCP client config (generic Claude/Cursor/Codex style)

Plug-and-play entry — no config file:

```json
{
  "mcpServers": {
    "toolpipe": {
      "command": "toolpipe",
      "args": ["serve"]
    }
  }
}
```

Pinned-config entry:

```json
{
  "mcpServers": {
    "toolpipe": {
      "command": "toolpipe",
      "args": ["serve", "--config", "/path/to/toolpipe.toml"]
    }
  }
}
```

## Consent scopes

| Scope | Meaning | Stored in |
| ----- | ------- | --------- |
| `once` | This one call only | Memory |
| `session` | This ToolPipe process | Memory |
| `workspace` | This project, across restarts | `<storage_dir>/servers.json` |
| `always` | Everywhere, across restarts | `~/.toolpipe/servers.json` |

Approvals persist connect info with `${VAR}` references unresolved (resolved
secrets are never written to disk). Removing a server revokes it everywhere
but keeps stored results. If your client cannot render approval prompts, the
agent asks in chat and calls `toolpipe_approve_server` instead.

## Control tools

| Tool | Purpose |
| ---- | ------- |
| `toolpipe_inspect_result` | Metadata + bounded preview for a `res_*` ref |
| `toolpipe_read_result` | Bounded byte-slice reads (diagnostic; prefer select for JSON) |
| `toolpipe_select_result` | JMESPath selection, e.g. `customers[].revenue` (small→inline, large→new ref) |
| `toolpipe_search_result` | Substring/regex search; line matches for text, leaf paths for JSON |
| `toolpipe_release_result` | Drop a stored result immediately |
| `toolpipe_pipe_result` | Pipe mapped data directly into a downstream tool |
| `toolpipe_pending_servers` | Discovered servers waiting for approval |
| `toolpipe_approve_server` | Approve a server (`once`/`session`/`workspace`/`always`) |
| `toolpipe_add_server` | Manually register a server discovery cannot see |
| `toolpipe_remove_server` | Revoke a server (stored results are kept) |
| `toolpipe_list_servers` | Servers with scope, status, tool counts (env redacted) |
| `toolpipe_set_policy` | `auto`/`always`/`never` per `server` or `server:tool` |
| `toolpipe_get_policy` | Configured policies (default is `auto`) |

Selectors are JMESPath only — no Python/Jinja/shell evaluation, ever.

## CLI

```bash
toolpipe serve [--config toolpipe.toml] [--log-level INFO]
toolpipe serve --import-mcp-json mcp.json --import-codex-toml config.toml
toolpipe serve --no-discover --no-dynamic-servers
toolpipe inspect res_abc123 --config toolpipe.toml
toolpipe results --config toolpipe.toml
toolpipe stats --config toolpipe.toml     # includes "estimated bytes avoided"
toolpipe clean --config toolpipe.toml     # expired results only
```

Logs go to stderr (stdout is the MCP protocol channel). Payloads and secrets
are never logged.

## Demo

```bash
uv run toolpipe serve --config examples/toolpipe.toml
```

Then, from any MCP client: `producer_generate_customers(count=100000)` returns
a `res_*` reference; `toolpipe_pipe_result` with mapping
`{"values": "customers[].revenue"}` into `analytics_calculate_statistics`
returns the stats without the array crossing context.

## Limitations

- STDIO upstream only; one process = one result scope (no multi-user mode).
- JSON and text virtualization only — images/audio/binary pass through.
- No CSV/row operations, no streaming, no cross-hose shared refs.
- Storage pressure fails loudly instead of evicting (`StorageLimitError`); no LRU.
- No `save_result(path=...)` by design (avoids path-traversal policy).

## Security

- JMESPath only; `eval`/`exec`/shell/Jinja/JS are not supported anywhere.
- Refs are opaque random tokens, never filesystem paths or hashes.
- Pipe targets must be approved downstream tools; `toolpipe_*` control tools are rejected.
- First use asks for approval showing the exact command; `deny` persists nothing.
- Scope files keep `${VAR}` references unresolved; listings redact env values.
- `never` policy disables virtualization only — a context-bloat footgun, not a data leak.
- All size/TTL limits enforced; `stdout` carries protocol output only.

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv build
```

See `CHANGELOG.md` for release history.
