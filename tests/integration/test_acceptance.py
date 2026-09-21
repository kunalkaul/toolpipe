"""End-to-end acceptance test with the demo producer/analytics pair."""

import json
import sys
from pathlib import Path

import pytest
from fastmcp import Client

from toolpipe.config import load_config
from toolpipe.server.app import ToolPipeApp, create_app

FIXTURES = Path(__file__).parent.parent / "fixtures"
COUNT = 100000


async def _make_app(tmp_path) -> ToolPipeApp:
    cfg_file = tmp_path / "toolpipe.toml"
    cfg_file.write_text(
        f"""
[results]
storage_dir = "{tmp_path / ".toolpipe"}"

[servers.producer]
command = "{sys.executable}"
args = ["{FIXTURES / "producer_mcp.py"}"]

[servers.analytics]
command = "{sys.executable}"
args = ["{FIXTURES / "analytics_mcp.py"}"]
"""
    )
    return await create_app(load_config(cfg_file))


async def test_acceptance_100k_customers(tmp_path, tmp_path_factory):
    app = await _make_app(tmp_path)
    try:
        async with Client(app.mcp) as client:
            produced = await client.call_tool("producer_generate_customers", {"count": COUNT})
            assert produced.structured_content is not None
            envelope = produced.structured_content["toolpipe"]
            ref_a = envelope["ref"]

            # (2) Upstream producer response stays under the reference budget.
            upstream_bytes = len(json.dumps(produced.structured_content).encode())
            assert upstream_bytes < 8192
            assert upstream_bytes < envelope["size_bytes"] // 100

            # (1) Full customer payload exists in ToolPipe storage.
            _, raw_payload = app.manager.payload_text(ref_a)
            assert len(json.loads(raw_payload)["customers"]) == COUNT

            # Inspect shows the array shape without the data.
            inspected = await client.call_tool("toolpipe_inspect_result", {"ref": ref_a})
            assert inspected.structured_content is not None
            preview = inspected.structured_content["preview"]
            assert preview["customers"]["length"] == COUNT

            # (3, 4) Pipe revenues straight into analytics; no array upstream.
            piped = await client.call_tool(
                "toolpipe_pipe_result",
                {
                    "ref": ref_a,
                    "target_tool": "analytics_calculate_statistics",
                    "mapping": {"values": "customers[].revenue"},
                },
            )
            assert piped.structured_content is not None
            assert piped.structured_content["count"] == COUNT
            assert "customers" not in piped.structured_content

            # (5) No arbitrary code evaluation: hostile expressions cannot run.
            marker = tmp_path_factory.mktemp("evil") / "pwned"
            with pytest.raises(Exception, match="validation|invalid|failed|error"):
                await client.call_tool(
                    "toolpipe_pipe_result",
                    {
                        "ref": ref_a,
                        "target_tool": "analytics_calculate_statistics",
                        "mapping": {"values": f"__import__('os').system('touch {marker}')"},
                    },
                )
            assert not marker.exists()

            # (6) Analytics errors propagate to the caller.
            with pytest.raises(Exception, match="validation|invalid|failed|error"):
                await client.call_tool(
                    "toolpipe_pipe_result",
                    {
                        "ref": ref_a,
                        "target_tool": "analytics_calculate_statistics",
                        "mapping": {"values": "@"},
                    },
                )

            # (7) A large analytics response is virtualized into res_B.
            big = await client.call_tool(
                "toolpipe_pipe_result",
                {
                    "ref": ref_a,
                    "target_tool": "analytics_big_echo",
                    "mapping": {"values": "customers[].revenue"},
                },
            )
            assert big.structured_content is not None
            ref_b = big.structured_content["toolpipe"]["ref"]
            assert big.structured_content["toolpipe"]["virtualized"] is True
            assert ref_b != ref_a
    finally:
        app.close()
