"""Virtualization middleware tests. In-memory, no subprocess."""

import json

import pytest
from fastmcp import Client, FastMCP
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp import types as mt
from mcp.types import ImageContent, TextContent

from toolpipe.models import ResultSettings
from toolpipe.results.manager import ResultManager
from toolpipe.server.virtualization import ResultVirtualizationMiddleware


def _setup(tmp_path, **settings_overrides):
    settings = ResultSettings(
        storage_dir=str(tmp_path / ".toolpipe"), inline_max_bytes=1024, **settings_overrides
    )
    manager = ResultManager.from_storage_dir(settings.storage_dir, settings)
    mcp = FastMCP("virt-test")

    @mcp.tool
    def small() -> dict:
        return {"ok": True}

    @mcp.tool
    def big() -> dict:
        return {"blob": "x" * 4096}

    @mcp.tool
    def binary() -> ImageContent:
        return ImageContent(type="image", data="e30=", mime_type="image/png")

    @mcp.tool
    def failing() -> str:
        raise RuntimeError("downstream blew up")

    mcp.add_middleware(ResultVirtualizationMiddleware(manager, settings))
    return mcp, manager


async def test_small_result_passes_through(tmp_path):
    mcp, manager = _setup(tmp_path)
    try:
        async with Client(mcp) as client:
            result = await client.call_tool("small", {})
        assert result.structured_content == {"ok": True}
        assert manager.stats()["stored_results"] == 0
    finally:
        manager.close()


async def test_large_json_virtualizes_and_round_trips(tmp_path):
    mcp, manager = _setup(tmp_path)
    try:
        async with Client(mcp) as client:
            result = await client.call_tool("big", {})
        envelope = result.structured_content or {}
        assert envelope["toolpipe"]["virtualized"] is True
        ref = envelope["toolpipe"]["ref"]
        assert ref.startswith("res_")
        assert envelope["toolpipe"]["size_bytes"] == len(b'{"blob":"' + b"x" * 4096 + b'"}')
        stored = json.loads(manager._store.read_bytes(ref).decode())
        assert stored == {"blob": "x" * 4096}
        assert any(isinstance(c, TextContent) and ref in c.text for c in result.content)
    finally:
        manager.close()


async def test_large_text_virtualizes(tmp_path):
    # Content-only text results (no structured content) take the text path.
    # Exercised at the middleware level with a stubbed downstream call.
    settings = ResultSettings(storage_dir=str(tmp_path / ".toolpipe"), inline_max_bytes=8)
    manager = ResultManager.from_storage_dir(settings.storage_dir, settings)
    middleware = ResultVirtualizationMiddleware(manager, settings)
    payload_text = "y" * 4096

    async def fake_downstream(context):
        return ToolResult(content=[TextContent(type="text", text=payload_text)])

    try:
        context = MiddlewareContext(
            message=mt.CallToolRequestParams(name="some_tool", arguments={})
        )
        result = await middleware.on_call_tool(context, fake_downstream)
        structured = result.structured_content or {}
        assert structured["toolpipe"]["content_type"] == "text/plain"
        ref = structured["toolpipe"]["ref"]
        assert manager._store.read_bytes(ref) == payload_text.encode()
    finally:
        manager.close()


async def test_errors_and_binary_pass_through(tmp_path):
    mcp, manager = _setup(tmp_path)
    try:
        async with Client(mcp) as client:
            with pytest.raises(Exception, match="downstream blew up"):
                await client.call_tool("failing", {})
            result = await client.call_tool("binary", {})
        assert not (result.structured_content or {}).get("toolpipe")
        assert manager.stats()["stored_results"] == 0
    finally:
        manager.close()


async def test_non_virtualizable_tools_skipped(tmp_path):
    settings = ResultSettings(storage_dir=str(tmp_path / ".toolpipe"), inline_max_bytes=8)
    manager = ResultManager.from_storage_dir(settings.storage_dir, settings)
    mcp = FastMCP("control-test")

    @mcp.tool(tags={"toolpipe-control"})
    def toolpipe_inspect_result(ref: str) -> dict:
        return {"ref": ref, "pad": "z" * 4096}

    mcp.add_middleware(
        ResultVirtualizationMiddleware(manager, settings, is_virtualizable=lambda name: False)
    )
    try:
        async with Client(mcp) as client:
            result = await client.call_tool("toolpipe_inspect_result", {"ref": "res_x"})
        assert result.structured_content is not None and "toolpipe" not in result.structured_content
        assert manager.stats()["stored_results"] == 0
    finally:
        manager.close()


async def test_storage_limit_failure_raises_clear_error(tmp_path):
    mcp, manager = _setup(tmp_path, max_result_bytes=10)
    try:
        async with Client(mcp) as client:
            # Over-limit payloads fail loudly instead of dumping bytes
            # into context or returning a dangling reference.
            with pytest.raises(Exception, match="exceeding"):
                await client.call_tool("big", {})
        assert manager.stats()["stored_results"] == 0
    finally:
        manager.close()


def test_reference_envelope_shape(tmp_path):
    from toolpipe.server.virtualization import build_reference_tool_result

    settings = ResultSettings(storage_dir=str(tmp_path / ".toolpipe"))
    manager = ResultManager.from_storage_dir(settings.storage_dir, settings)
    try:
        stored = manager.store(
            b'{"a":1}', "application/json", "some_tool", preview={"type": "object"}
        )
        response = build_reference_tool_result(stored, {"type": "object"})
        assert isinstance(response, ToolResult)
        assert response.structured_content == {
            "toolpipe": {
                "virtualized": True,
                "ref": stored.ref,
                "content_type": "application/json",
                "size_bytes": 7,
                "source_tool": "some_tool",
                "expires_at": stored.expires_at,
                "preview": {"type": "object"},
            }
        }
    finally:
        manager.close()
