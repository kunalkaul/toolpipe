"""Bounded, deterministic, LLM-free previews."""

from __future__ import annotations

import json
from typing import Any

from toolpipe.results.codec import JSON_CONTENT_TYPE, TEXT_CONTENT_TYPE

DEFAULT_MAX_DEPTH = 3
DEFAULT_MAX_KEYS = 20
DEFAULT_MAX_SAMPLES = 2
DEFAULT_MAX_STRING = 200


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _summarize(value: Any, depth: int, max_keys: int, max_samples: int, max_string: int) -> Any:
    if isinstance(value, dict):
        if depth <= 0:
            return {"type": "object", "truncated": True}
        keys = list(value.keys())[:max_keys]
        summary: dict[str, Any] = {"type": "object", "keys": keys}
        for key in keys:
            child = value[key]
            if isinstance(child, dict | list):
                summary[key] = _summarize(child, depth - 1, max_keys, max_samples, max_string)
            elif isinstance(child, str):
                summary[key] = child[:max_string]
            else:
                summary[key] = child
        return summary
    if isinstance(value, list):
        if depth <= 0:
            return {"type": "array", "length": len(value), "truncated": True}
        return {
            "type": "array",
            "length": len(value),
            "sample": [
                _summarize(item, depth - 1, max_keys, max_samples, max_string)
                if isinstance(item, (dict, list))
                else (item[:max_string] if isinstance(item, str) else item)
                for item in value[:max_samples]
            ],
        }
    if isinstance(value, str):
        return value[:max_string]
    return value


def _fits(preview: Any, budget: int) -> bool:
    return len(json.dumps(preview, ensure_ascii=False).encode("utf-8")) <= budget


def build_preview(
    preview_source: Any,
    content_type: str,
    preview_max_bytes: int,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> dict:
    """Build a preview guaranteed to fit within preview_max_bytes."""
    if content_type == JSON_CONTENT_TYPE:
        for max_keys, max_samples in (
            (DEFAULT_MAX_KEYS, DEFAULT_MAX_SAMPLES),
            (5, 1),
        ):
            summarized = _summarize(
                preview_source, max_depth, max_keys, max_samples, DEFAULT_MAX_STRING
            )
            preview = (
                summarized
                if isinstance(summarized, dict)
                else {"type": _json_type(preview_source), "value": summarized}
            )
            if _fits(preview, preview_max_bytes):
                return preview
        return {"type": _json_type(preview_source), "truncated": True}
    if content_type == TEXT_CONTENT_TYPE:
        text = preview_source if isinstance(preview_source, str) else ""
        preview = {
            "type": "text",
            "characters": len(text),
            "lines_estimate": text.count("\n") + 1 if text else 0,
            "head": "",
        }
        overhead = len(json.dumps(preview, ensure_ascii=False).encode("utf-8"))
        head = text.encode("utf-8")[: max(0, preview_max_bytes - overhead)].decode(
            "utf-8", errors="ignore"
        )
        preview["head"] = head
        # JSON escaping can expand head bytes; shrink until it fits (or empties).
        while not _fits(preview, preview_max_bytes) and preview["head"]:
            raw = preview["head"].encode("utf-8")
            preview["head"] = raw[: len(raw) // 2].decode("utf-8", errors="ignore")
        return preview
    raise ValueError(f"Cannot preview content type: {content_type}")
