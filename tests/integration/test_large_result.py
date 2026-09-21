"""End-to-end large-result virtualization through ToolPipe (STDIO downstream)."""

import json
import sys
from pathlib import Path

from fastmcp import Client

from toolpipe.config import load_config
from toolpipe.server.app import create_app

FIXTURES = Path(__file__).parent.parent / "fixtures"


async def test_large_downstream_result_becomes_compact_ref(tmp_path):
    cfg_file = tmp_path / "toolpipe.toml"
    cfg_file.write_text(
        f"""
[results]
storage_dir = "{tmp_path / ".toolpipe"}"

[servers.echo_a]
command = "{sys.executable}"
args = ["{FIXTURES / "echo_a_mcp.py"}"]
"""
    )
    app = await create_app(load_config(cfg_file))
    try:
        async with Client(app.mcp) as client:
            result = await client.call_tool("echo_a_big_payload", {"size_kb": 64})
        envelope = (result.structured_content or {})["toolpipe"]
        assert envelope["virtualized"] is True
        ref = envelope["ref"]
        # Upstream response stays compact while the payload is stored locally.
        upstream_bytes = len(json.dumps(result.structured_content).encode())
        assert upstream_bytes < 4096
        assert envelope["size_bytes"] > 64 * 1024
        payload = json.loads(app.manager._store.read_bytes(ref).decode())
        assert payload == {"blob": "x" * 64 * 1024}

        async with Client(app.mcp) as client:
            text_result = await client.call_tool("echo_a_big_text", {"size_kb": 64})
        # FastMCP wraps str returns as structured content, so str tools take
        # the JSON path; the payload must still be stored, not inlined.
        text_envelope = (text_result.structured_content or {})["toolpipe"]
        assert text_envelope["virtualized"] is True
        assert text_envelope["size_bytes"] > 64 * 1024
    finally:
        app.close()
