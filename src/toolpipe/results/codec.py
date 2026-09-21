"""Normalize FastMCP ToolResults into storable payloads.

The virtualization middleware must not contain encoding logic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from fastmcp.tools.base import ToolResult
from mcp.types import TextContent

JSON_CONTENT_TYPE = "application/json"
TEXT_CONTENT_TYPE = "text/plain"
TEXT_BLOCK_SEPARATOR = "\n"


@dataclass(frozen=True)
class NormalizedResult:
    supported: bool
    content_type: str | None
    payload: bytes | None
    parsed_value: Any | None
    size_bytes: int
    preview_source: Any | None


def normalize(result: ToolResult) -> NormalizedResult:
    """Convert a ToolResult into a storable representation (serialize once)."""
    if result.structured_content is not None:
        payload = json.dumps(
            result.structured_content,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return NormalizedResult(
            supported=True,
            content_type=JSON_CONTENT_TYPE,
            payload=payload,
            parsed_value=result.structured_content,
            size_bytes=len(payload),
            preview_source=result.structured_content,
        )
    texts: list[str] = []
    for block in result.content:
        if not isinstance(block, TextContent):
            return NormalizedResult(
                supported=False,
                content_type=None,
                payload=None,
                parsed_value=None,
                size_bytes=0,
                preview_source=None,
            )
        texts.append(block.text)
    if not texts:
        return NormalizedResult(
            supported=False,
            content_type=None,
            payload=None,
            parsed_value=None,
            size_bytes=0,
            preview_source=None,
        )
    text = TEXT_BLOCK_SEPARATOR.join(texts)
    payload = text.encode("utf-8")
    return NormalizedResult(
        supported=True,
        content_type=TEXT_CONTENT_TYPE,
        payload=payload,
        parsed_value=text,
        size_bytes=len(payload),
        preview_source=text,
    )
