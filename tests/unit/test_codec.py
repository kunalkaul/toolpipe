"""Codec + preview + selector + search tests."""

import json

import pytest
from fastmcp.tools.base import ToolResult
from mcp.types import ImageContent, TextContent

from toolpipe.errors import SelectionError
from toolpipe.models import ResultSettings
from toolpipe.results import search as result_search
from toolpipe.results import selector
from toolpipe.results.codec import normalize
from toolpipe.results.manager import ResultManager
from toolpipe.results.preview import build_preview


def _manager(tmp_path, **overrides) -> ResultManager:
    return ResultManager.from_storage_dir(str(tmp_path / ".toolpipe"), ResultSettings(**overrides))


# -- codec --


def test_normalize_structured_json():
    result = ToolResult(structured_content={"customers": [{"id": 1}]})
    norm = normalize(result)
    assert norm.supported and norm.content_type == "application/json"
    assert norm.payload == b'{"customers":[{"id":1}]}'
    assert norm.size_bytes == len(norm.payload or b"")
    assert norm.parsed_value == {"customers": [{"id": 1}]}


def test_normalize_text_single_and_multiple_blocks():
    single = ToolResult(content=[TextContent(type="text", text="hello")])
    assert normalize(single).payload == b"hello"
    multi = ToolResult(
        content=[
            TextContent(type="text", text="a"),
            TextContent(type="text", text="b"),
        ]
    )
    norm = normalize(multi)
    assert norm.supported and norm.payload == b"a\nb"


def test_normalize_rejects_binary_and_mixed():
    binary = ToolResult(content=[ImageContent(type="image", data="e30=", mime_type="image/png")])
    assert not normalize(binary).supported
    mixed = ToolResult(
        content=[
            TextContent(type="text", text="x"),
            ImageContent(type="image", data="e30=", mime_type="image/png"),
        ]
    )
    assert not normalize(mixed).supported


def test_normalize_unicode_byte_size():
    result = ToolResult(content=[TextContent(type="text", text="héllo ✓")])
    norm = normalize(result)
    assert norm.size_bytes == len("héllo ✓".encode())


# -- preview --


def test_json_preview_nested_object():
    value = {"customers": [{"id": 1, "name": "Alice"}], "page": 1}
    preview = build_preview(value, "application/json", 4096)
    assert preview["type"] == "object"
    assert preview["keys"] == ["customers", "page"]
    assert preview["customers"] == {
        "type": "array",
        "length": 1,
        "sample": [{"type": "object", "keys": ["id", "name"], "id": 1, "name": "Alice"}],
    }
    assert preview["page"] == 1


def test_json_preview_huge_array_bounded():
    value = {"rows": [{"i": i, "pad": "x" * 500} for i in range(10000)]}
    preview = build_preview(value, "application/json", 4096)
    raw = json.dumps(preview, ensure_ascii=False).encode()
    assert len(raw) <= 4096
    assert preview["rows"]["length"] == 10000
    assert len(preview["rows"]["sample"]) <= 2


def test_json_preview_huge_string_truncated():
    preview = build_preview({"blob": "y" * 100000}, "application/json", 4096)
    assert len(json.dumps(preview).encode()) <= 4096


def test_text_preview_head_and_counts():
    text = "line1\nline2\nline3"
    preview = build_preview(text, "text/plain", 4096)
    assert preview == {
        "type": "text",
        "characters": len(text),
        "lines_estimate": 3,
        "head": text,
    }


def test_text_preview_head_bounded():
    preview = build_preview("z" * 100000, "text/plain", 4096)
    assert len(json.dumps(preview).encode()) <= 4096
    assert preview["characters"] == 100000


# -- selector --


def test_selector_basic_and_invalid():
    value = {"customers": [{"revenue": 10}, {"revenue": 20}]}
    assert selector.evaluate(value, "customers[].revenue") == [10, 20]
    assert selector.evaluate(value, "@") == value
    with pytest.raises(SelectionError, match="invalid"):
        selector.evaluate(value, "customers[.")


# -- search --


def test_search_text_lines():
    out = result_search.search_text("ok\nconnection refused by upstream\nok", "refused")
    assert out == {
        "matches": [{"line": 2, "text": "connection refused by upstream"}],
        "truncated": False,
    }


def test_search_text_regex_and_bad_regex():
    out = result_search.search_text("abc 123", r"\d+", regex=True)
    assert len(out["matches"]) == 1
    with pytest.raises(SelectionError, match="[Ii]nvalid regular expression"):
        result_search.search_text("abc", "([", regex=True)


def test_search_json_leaves():
    value = {"customers": [{"status": "ok"}, {"status": "connection refused"}]}
    out = result_search.search_json(value, "refused")
    assert out["matches"] == [{"path": "customers[1].status", "value": "connection refused"}]


def test_search_limit_truncates():
    text = "\n".join(f"hit {i}" for i in range(50))
    out = result_search.search_text(text, "hit", limit=5)
    assert len(out["matches"]) == 5
    assert out["truncated"] is True


def test_search_deep_nesting_rejected_not_crash():
    value: dict = {}
    node = value
    for i in range(2000):
        node["child"] = {}
        node = node["child"]
    node["leaf"] = "needle"
    with pytest.raises(SelectionError, match="nesting"):
        result_search.search_json(value, "needle")


def test_search_exact_end_not_truncated():
    out = result_search.search_json({"a": "hit"}, "hit", limit=20)
    assert len(out["matches"]) == 1
    assert out["truncated"] is False


def test_text_preview_fits_small_budget():
    preview = build_preview("z" * 100000, "text/plain", 128)
    assert len(json.dumps(preview).encode()) <= 128
    assert preview["characters"] == 100000


def test_text_preview_shrinks_to_minimal_when_budget_tiny():
    # Metadata overhead alone exceeds a 10-byte budget; head must empty out.
    preview = build_preview("z" * 100000, "text/plain", 10)
    assert preview["head"] == ""
    assert preview["characters"] == 100000


# -- manager select/search --


def test_manager_select_inline_scalar(tmp_path):
    mgr = _manager(tmp_path)
    stored = mgr.store(b'{"a":{"b":42}}', "application/json", "t")
    sel = mgr.select(stored.ref, "a.b")
    assert (sel.virtualized, sel.value, sel.ref) == (False, 42, stored.ref)
    mgr.close()


def test_manager_select_large_value_virtualizes(tmp_path):
    mgr = _manager(tmp_path, inline_max_bytes=16)
    stored = mgr.store(b'{"rows":[1,2,3,4,5,6,7,8]}', "application/json", "t")
    sel = mgr.select(stored.ref, "rows")
    assert sel.virtualized and sel.ref != stored.ref and sel.value is None
    assert mgr.get(sel.ref).size_bytes == sel.size_bytes
    mgr.close()


def test_manager_select_rejects_text(tmp_path):
    mgr = _manager(tmp_path)
    stored = mgr.store(b"just text", "text/plain", "t")
    with pytest.raises(SelectionError, match="requires application/json"):
        mgr.select(stored.ref, "@")
    mgr.close()


def test_manager_search_dispatch(tmp_path):
    mgr = _manager(tmp_path)
    t = mgr.store(b"alpha\nbeta", "text/plain", "t")
    assert mgr.search(t.ref, "beta")["matches"] == [{"line": 2, "text": "beta"}]
    j = mgr.store(b'{"s":"beta here"}', "application/json", "t")
    assert mgr.search(j.ref, "beta")["matches"] == [{"path": "s", "value": "beta here"}]
    mgr.close()
