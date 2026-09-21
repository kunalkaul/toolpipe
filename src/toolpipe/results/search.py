"""Substring/regex search over text and JSON leaves.

No semantic/vector search. Conservative caps.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from toolpipe.errors import SelectionError

QUERY_MAX_LENGTH = 200
SCAN_MAX_BYTES = 10 * 1024 * 1024
MATCH_LIMIT_MAX = 100
WALK_MAX_DEPTH = 100


@dataclass(frozen=True)
class TextMatch:
    line: int
    text: str


@dataclass(frozen=True)
class JsonMatch:
    path: str
    value: str


def _compile(query: str, regex: bool) -> re.Pattern[str] | None:
    if len(query) > QUERY_MAX_LENGTH:
        raise SelectionError(f"Search query exceeds {QUERY_MAX_LENGTH} characters; narrow it down.")
    if not regex:
        return None
    try:
        return re.compile(query)
    except re.error as e:
        raise SelectionError(f"Invalid regular expression `{query}`: {e}")


def _matches(pattern: re.Pattern[str] | None, query: str, text: str) -> bool:
    if pattern is not None:
        return pattern.search(text) is not None
    return query in text


def search_text(text: str, query: str, regex: bool = False, limit: int = 20) -> dict:
    pattern = _compile(query, regex)
    limit = min(max(limit, 1), MATCH_LIMIT_MAX)
    matches: list[dict] = []
    scanned_bytes = 0
    lines = text.splitlines()
    truncated = False
    for lineno, line in enumerate(lines, start=1):
        scanned_bytes += len(line.encode("utf-8")) + 1
        if scanned_bytes > SCAN_MAX_BYTES:
            truncated = True
            break
        if _matches(pattern, query, line):
            matches.append({"line": lineno, "text": line[:500]})
            if len(matches) >= limit:
                truncated = lineno < len(lines)
                break
    return {"matches": matches, "truncated": truncated}


def _iter_leaves(value: Any) -> Any:
    """Yield (path, scalar) leaves iteratively with a depth cap.

    Raises SelectionError on nesting deeper than WALK_MAX_DEPTH instead of
    crashing with RecursionError.
    """
    stack: list[tuple[Any, str, int]] = [(value, "", 0)]
    while stack:
        node, path, depth = stack.pop()
        if isinstance(node, dict):
            if depth >= WALK_MAX_DEPTH:
                raise SelectionError(
                    f"JSON nesting exceeds {WALK_MAX_DEPTH} levels; cannot search."
                )
            for key in reversed(list(node.keys())):
                child_path = f"{path}.{key}" if path else str(key)
                stack.append((node[key], child_path, depth + 1))
        elif isinstance(node, list):
            if depth >= WALK_MAX_DEPTH:
                raise SelectionError(
                    f"JSON nesting exceeds {WALK_MAX_DEPTH} levels; cannot search."
                )
            for i in reversed(range(len(node))):
                stack.append((node[i], f"{path}[{i}]", depth + 1))
        elif isinstance(node, str | int | float | bool) or node is None:
            yield (path or "@", node)


def search_json(value: Any, query: str, regex: bool = False, limit: int = 20) -> dict:
    pattern = _compile(query, regex)
    limit = min(max(limit, 1), MATCH_LIMIT_MAX)
    matches: list[dict] = []
    truncated = False
    scanned_bytes = 0
    walker = _iter_leaves(value)
    for path, leaf in walker:
        text = leaf if isinstance(leaf, str) else str(leaf)
        scanned_bytes += len(text.encode("utf-8")) + len(path.encode("utf-8"))
        if scanned_bytes > SCAN_MAX_BYTES:
            truncated = True
            break
        if _matches(pattern, query, text):
            matches.append({"path": path, "value": text[:500]})
            if len(matches) >= limit:
                # Exact: truncated only if unvisited input remains.
                try:
                    next(walker)
                except StopIteration:
                    truncated = False
                else:
                    truncated = True
                break
    return {"matches": matches, "truncated": truncated}
