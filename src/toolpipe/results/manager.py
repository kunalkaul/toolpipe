"""Central application service for stored results.

All ToolPipe MCP tools must call this layer, never SQLite/files directly.
"""

from __future__ import annotations

import json

from toolpipe.errors import SelectionError
from toolpipe.models import (
    ReadResult,
    ResultInfo,
    ResultSettings,
    SelectionResult,
    StoredResult,
)
from toolpipe.results import search as result_search
from toolpipe.results import selector
from toolpipe.results.codec import JSON_CONTENT_TYPE, TEXT_CONTENT_TYPE
from toolpipe.results.preview import build_preview
from toolpipe.results.store import ResultStore


class ResultManager:
    def __init__(self, store: ResultStore, settings: ResultSettings) -> None:
        self._store = store
        self._settings = settings

    @classmethod
    def from_storage_dir(cls, storage_dir: str, settings: ResultSettings) -> ResultManager:
        return cls(ResultStore(storage_dir, settings), settings)

    def close(self) -> None:
        self._store.close()

    def incr_counter(self, name: str, delta: int = 1) -> int:
        return self._store.incr_counter(name, delta)

    def store(
        self,
        payload: bytes,
        content_type: str,
        source_tool: str | None,
        source_server: str | None = None,
        preview: dict | None = None,
    ) -> StoredResult:
        stored = self._store.store(payload, content_type, source_tool, source_server, preview)
        self._store.incr_counter("virtualized_results")
        self._store.incr_counter("virtualized_bytes", stored.size_bytes)
        return stored

    def get(self, ref: str) -> StoredResult:
        return self._store.get(ref)

    def read(self, ref: str, offset: int = 0, limit: int = 8192) -> ReadResult:
        if offset < 0:
            raise SelectionError("Read offset must be >= 0.")
        if limit < 0:
            raise SelectionError("Read limit must be >= 0.")
        limit = min(limit, self._settings.read_max_bytes)
        stored = self._store.get(ref)
        data = self._store.read_bytes_for(stored)
        chunk = data[offset : offset + limit]
        return ReadResult(
            ref=ref,
            offset=offset,
            returned_bytes=len(chunk),
            has_more=offset + len(chunk) < len(data),
            content=chunk.decode("utf-8", errors="replace"),
            content_type=stored.content_type,
        )

    def inspect(self, ref: str) -> ResultInfo:
        stored = self._store.get(ref)
        return ResultInfo(
            ref=stored.ref,
            content_type=stored.content_type,
            size_bytes=stored.size_bytes,
            source_tool=stored.source_tool,
            created_at=stored.created_at,
            expires_at=stored.expires_at,
            preview_json=stored.preview_json,
        )

    def payload_text(self, ref: str) -> tuple[StoredResult, str]:
        """Return (row, full UTF-8 payload) with a single access bump."""
        stored = self._store.get(ref)
        return stored, self._store.read_bytes_for(stored).decode("utf-8")

    def release(self, ref: str) -> bool:
        ok = self._store.release(ref)
        self._store.incr_counter("released_results")
        return ok

    def select(self, ref: str, expression: str) -> SelectionResult:
        stored = self._store.get(ref)
        if stored.content_type != JSON_CONTENT_TYPE:
            raise SelectionError(
                f"Result is {stored.content_type}; JMESPath selection requires {JSON_CONTENT_TYPE}."
            )
        value = json.loads(self._store.read_bytes_for(stored).decode("utf-8"))
        selected = selector.evaluate(value, expression)
        serialized = json.dumps(selected, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(serialized) <= self._settings.inline_max_bytes:
            return SelectionResult(
                ref=ref,
                virtualized=False,
                content_type=JSON_CONTENT_TYPE,
                size_bytes=len(serialized),
                value=selected,
            )
        preview = build_preview(selected, JSON_CONTENT_TYPE, self._settings.preview_max_bytes)
        new_ref = self.store(
            serialized,
            JSON_CONTENT_TYPE,
            source_tool=stored.source_tool,
            source_server=stored.source_server,
            preview=preview,
        )
        self._store.incr_counter("selected_results")
        return SelectionResult(
            ref=new_ref.ref,
            virtualized=True,
            content_type=JSON_CONTENT_TYPE,
            size_bytes=len(serialized),
            value=None,
        )

    def search(self, ref: str, query: str, regex: bool = False, limit: int = 20) -> dict:
        stored, raw = self.payload_text(ref)
        if stored.content_type == TEXT_CONTENT_TYPE:
            return result_search.search_text(raw, query, regex=regex, limit=limit)
        if stored.content_type == JSON_CONTENT_TYPE:
            return result_search.search_json(json.loads(raw), query, regex=regex, limit=limit)
        raise SelectionError(f"Cannot search content type: {stored.content_type}.")

    def cleanup_expired(self) -> int:
        count = self._store.cleanup_expired()
        if count:
            self._store.incr_counter("expired_results", count)
        return count

    def list_results(self) -> list[StoredResult]:
        return self._store.list_all()

    def stats(self) -> dict:
        virtualized_bytes = self._store.get_counter("virtualized_bytes")
        reference_bytes = self._store.get_counter("reference_response_bytes")
        return {
            "proxied_tool_calls": self._store.get_counter("proxied_tool_calls"),
            "virtualized_results": self._store.get_counter("virtualized_results"),
            "virtualized_bytes": virtualized_bytes,
            "reference_response_bytes": reference_bytes,
            "estimated_bytes_avoided": virtualized_bytes - reference_bytes,
            "pipe_calls": self._store.get_counter("pipe_calls"),
            "stored_results": self._store.active_count(),
            "current_storage_bytes": self._store.store_size_bytes(),
            "released_results": self._store.get_counter("released_results"),
            "expired_results": self._store.get_counter("expired_results"),
            "selected_results": self._store.get_counter("selected_results"),
        }

    def preview_of(self, ref: str) -> dict | None:
        stored = self._store.get(ref)
        if stored.preview_json is None:
            return None
        return json.loads(stored.preview_json)
