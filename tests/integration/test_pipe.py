"""Direct result piping tests."""

import json
import sys
from pathlib import Path

import pytest
from fastmcp import Client

from toolpipe.config import load_config
from toolpipe.server.app import ToolPipeApp, create_app

FIXTURES = Path(__file__).parent.parent / "fixtures"


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


async def _produce_ref(app: ToolPipeApp, client: Client, count: int = 100000) -> str:
    if count >= 50000:
        result = await client.call_tool("producer_generate_customers", {"count": count})
        assert result.structured_content is not None
        ref = result.structured_content["toolpipe"]["ref"]
        assert result.structured_content["toolpipe"]["size_bytes"] > 1000000
        return ref
    payload = json.dumps(
        {
            "customers": [
                {"id": i, "name": f"Customer {i}", "revenue": float(i)} for i in range(count)
            ]
        },
        separators=(",", ":"),
    ).encode()
    return app.manager.store(payload, "application/json", "producer_generate_customers").ref


async def test_pipe_core_proof_point(tmp_path):
    app = await _make_app(tmp_path)
    try:
        async with Client(app.mcp) as client:
            ref = await _produce_ref(app, client)
            piped = await client.call_tool(
                "toolpipe_pipe_result",
                {
                    "ref": ref,
                    "target_tool": "analytics_calculate_statistics",
                    "mapping": {"values": "customers[].revenue"},
                },
            )
            assert piped.structured_content is not None
            stats = piped.structured_content
            # Upstream never receives the customer array.
            assert "customers" not in stats
            assert stats == {"count": 100000, "min": 0.0, "max": 4999.0, "mean": 2499.5}
    finally:
        app.close()


async def test_pipe_literal_arguments(tmp_path):
    app = await _make_app(tmp_path)
    try:
        async with Client(app.mcp) as client:
            ref = await _produce_ref(app, client, count=100)
            piped = await client.call_tool(
                "toolpipe_pipe_result",
                {
                    "ref": ref,
                    "target_tool": "analytics_calculate_statistics",
                    "mapping": {"values": "customers[].revenue"},
                    "arguments": {"round_to": 0},
                },
            )
            assert piped.structured_content is not None
            assert piped.structured_content["mean"] == 50.0
    finally:
        app.close()


async def test_pipe_collision_rejected(tmp_path):
    app = await _make_app(tmp_path)
    try:
        async with Client(app.mcp) as client:
            ref = await _produce_ref(app, client, count=10)
            with pytest.raises(Exception, match="[Cc]ollision|both mapping and arguments"):
                await client.call_tool(
                    "toolpipe_pipe_result",
                    {
                        "ref": ref,
                        "target_tool": "analytics_calculate_statistics",
                        "mapping": {"values": "customers[].revenue"},
                        "arguments": {"values": [1.0]},
                    },
                )
    finally:
        app.close()


async def test_pipe_root_payload_and_text_root(tmp_path):
    app = await _make_app(tmp_path)
    try:
        async with Client(app.mcp) as client:
            ref = await _produce_ref(app, client, count=10)
            rooted = await client.call_tool(
                "toolpipe_pipe_result",
                {
                    "ref": ref,
                    "target_tool": "analytics_echo_records",
                    "mapping": {"records": "@"},
                },
            )
            assert rooted.structured_content == {"keys": ["customers"]}
        text_ref = app.manager.store(b"hello world", "text/plain", "t").ref
        async with Client(app.mcp) as client:
            echoed = await client.call_tool(
                "toolpipe_pipe_result",
                {
                    "ref": text_ref,
                    "target_tool": "analytics_echo_text",
                    "mapping": {"data": "@"},
                },
            )
            assert echoed.structured_content == {"length": 11}
    finally:
        app.close()


async def test_pipe_rejects_bad_targets(tmp_path):
    app = await _make_app(tmp_path)
    try:
        async with Client(app.mcp) as client:
            ref = await _produce_ref(app, client, count=10)
            with pytest.raises(Exception, match="cannot be used as a pipe target"):
                await client.call_tool(
                    "toolpipe_pipe_result",
                    {
                        "ref": ref,
                        "target_tool": "toolpipe_inspect_result",
                        "mapping": {"ref": "@"},
                    },
                )
            with pytest.raises(Exception, match="cannot be used as a pipe target"):
                await client.call_tool(
                    "toolpipe_pipe_result",
                    {"ref": ref, "target_tool": "nope_missing", "mapping": {}},
                )
    finally:
        app.close()


async def test_pipe_revirtualizes_large_target_output(tmp_path):
    app = await _make_app(tmp_path)
    try:
        async with Client(app.mcp) as client:
            ref = await _produce_ref(app, client, count=1000)
            piped = await client.call_tool(
                "toolpipe_pipe_result",
                {
                    "ref": ref,
                    "target_tool": "analytics_big_echo",
                    "mapping": {"values": "customers[].revenue"},
                },
            )
            assert piped.structured_content is not None
            envelope = piped.structured_content["toolpipe"]
            assert envelope["virtualized"] is True
            assert envelope["ref"] != ref
            assert envelope["size_bytes"] > 200000
    finally:
        app.close()
