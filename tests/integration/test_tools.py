"""Control-tool tests through a serverless ToolPipe app."""

import json

import pytest
from fastmcp import Client

from toolpipe.models import ResultSettings, ToolPipeConfig
from toolpipe.server.app import ToolPipeApp, create_app


async def _make_app(tmp_path) -> ToolPipeApp:
    config = ToolPipeConfig(results=ResultSettings(storage_dir=str(tmp_path / ".toolpipe")))
    return await create_app(config)


async def _store_json(app: ToolPipeApp) -> str:
    payload = json.dumps(
        {"customers": [{"id": 1, "status": "ok"}, {"id": 2, "status": "broken"}]},
        separators=(",", ":"),
    ).encode()
    return app.manager.store(payload, "application/json", "db_query").ref


async def test_control_tools_listed_and_not_downstream(tmp_path):
    app = await _make_app(tmp_path)
    try:
        async with Client(app.mcp) as client:
            names = {t.name for t in await client.list_tools()}
        for name in (
            "toolpipe_inspect_result",
            "toolpipe_read_result",
            "toolpipe_select_result",
            "toolpipe_search_result",
            "toolpipe_release_result",
        ):
            assert name in names
            assert not app.registry.is_downstream_tool(name)
    finally:
        app.close()


async def test_inspect_and_invalid_ref(tmp_path):
    app = await _make_app(tmp_path)
    try:
        ref = await _store_json(app)
        async with Client(app.mcp) as client:
            info = await client.call_tool("toolpipe_inspect_result", {"ref": ref})
            assert info.structured_content is not None
            assert info.structured_content["content_type"] == "application/json"
            assert info.structured_content["source_tool"] == "db_query"
            assert info.structured_content["preview"] is None  # stored without preview
            with pytest.raises(Exception, match="not found"):
                await client.call_tool("toolpipe_inspect_result", {"ref": "res_nope"})
    finally:
        app.close()


async def test_read_bounded_and_json_note(tmp_path):
    app = await _make_app(tmp_path)
    try:
        ref = await _store_json(app)
        async with Client(app.mcp) as client:
            page = await client.call_tool(
                "toolpipe_read_result", {"ref": ref, "offset": 0, "limit": 10}
            )
            assert page.structured_content is not None
            assert page.structured_content["returned_bytes"] == 10
            assert page.structured_content["has_more"] is True
            assert "select" in page.structured_content["note"]
            tail = await client.call_tool(
                "toolpipe_read_result", {"ref": ref, "offset": 100000, "limit": 10}
            )
            assert tail.structured_content is not None
            assert tail.structured_content["has_more"] is False
    finally:
        app.close()


async def test_select_inline_and_virtualized_and_errors(tmp_path):
    app = await _make_app(tmp_path)
    try:
        ref = await _store_json(app)
        async with Client(app.mcp) as client:
            scalar = await client.call_tool(
                "toolpipe_select_result", {"ref": ref, "expression": "customers[0].id"}
            )
            assert scalar.structured_content is not None
            assert scalar.structured_content["virtualized"] is False
            assert scalar.structured_content["value"] == 1
            with pytest.raises(Exception, match="invalid"):
                await client.call_tool(
                    "toolpipe_select_result", {"ref": ref, "expression": "customers[."}
                )
        text_ref = app.manager.store(b"plain", "text/plain", "t").ref
        async with Client(app.mcp) as client:
            with pytest.raises(Exception, match="requires application/json"):
                await client.call_tool(
                    "toolpipe_select_result", {"ref": text_ref, "expression": "@"}
                )
    finally:
        app.close()


async def test_select_large_value_returns_new_ref(tmp_path):
    config = ToolPipeConfig(
        results=ResultSettings(storage_dir=str(tmp_path / ".toolpipe"), inline_max_bytes=8)
    )
    app = await create_app(config)
    try:
        ref = app.manager.store(b'{"rows":[1,2,3,4,5]}', "application/json", "t").ref
        async with Client(app.mcp) as client:
            out = await client.call_tool(
                "toolpipe_select_result", {"ref": ref, "expression": "rows"}
            )
            assert out.structured_content is not None
            assert out.structured_content["virtualized"] is True
            assert out.structured_content["ref"].startswith("res_")
    finally:
        app.close()


async def test_search_text_and_json_and_bad_regex(tmp_path):
    app = await _make_app(tmp_path)
    try:
        text_ref = app.manager.store(b"a\nneedle here\nb", "text/plain", "t").ref
        json_ref = await _store_json(app)
        async with Client(app.mcp) as client:
            text = await client.call_tool(
                "toolpipe_search_result", {"ref": text_ref, "query": "needle"}
            )
            assert text.structured_content is not None
            assert text.structured_content["matches"] == [{"line": 2, "text": "needle here"}]
            js = await client.call_tool(
                "toolpipe_search_result", {"ref": json_ref, "query": "broken"}
            )
            assert js.structured_content is not None
            assert js.structured_content["matches"] == [
                {"path": "customers[1].status", "value": "broken"}
            ]
            with pytest.raises(Exception, match="[Ii]nvalid regular expression"):
                await client.call_tool(
                    "toolpipe_search_result",
                    {"ref": text_ref, "query": "([", "regex": True},
                )
    finally:
        app.close()


async def test_release_removes_access(tmp_path):
    app = await _make_app(tmp_path)
    try:
        ref = await _store_json(app)
        async with Client(app.mcp) as client:
            out = await client.call_tool("toolpipe_release_result", {"ref": ref})
            assert out.structured_content == {"released": True, "ref": ref}
            with pytest.raises(Exception, match="not found"):
                await client.call_tool("toolpipe_inspect_result", {"ref": ref})
    finally:
        app.close()
