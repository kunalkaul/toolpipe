"""SQLite metadata + filesystem blob store.

Layout: <storage_dir>/toolpipe.db + blobs/ab/cd/<sha256>.
Refs are opaque `res_*` tokens; never filesystem paths or hashes.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
from pathlib import Path

from toolpipe.constants import REF_PREFIX
from toolpipe.errors import ResultExpiredError, ResultNotFoundError, StorageLimitError
from toolpipe.models import ResultSettings, StoredResult
from toolpipe.utils import atomic
from toolpipe.utils.time import expires_iso, now_iso, parse_iso, utc_now

_SCHEMA = """
CREATE TABLE IF NOT EXISTS results (
    ref TEXT PRIMARY KEY,
    blob_sha256 TEXT NOT NULL,
    blob_path TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    source_tool TEXT,
    source_server TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_accessed_at TEXT,
    access_count INTEGER NOT NULL DEFAULT 0,
    preview_json TEXT
);
CREATE TABLE IF NOT EXISTS blobs (
    sha256 TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    ref_count INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS counters (
    name TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
"""


def generate_ref() -> str:
    return REF_PREFIX + secrets.token_urlsafe(16)


class ResultStore:
    """Thread-safe SQLite + file result store. No MCP dependency."""

    def __init__(self, storage_dir: str | Path, settings: ResultSettings) -> None:
        self._dir = Path(storage_dir)
        self._settings = settings
        self._lock = threading.Lock()
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / "blobs").mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self._dir / "toolpipe.db", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(_SCHEMA)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- paths --

    def _blob_path(self, sha256: str) -> Path:
        return self._dir / "blobs" / sha256[:2] / sha256[2:4] / sha256

    # -- counters --

    def incr_counter(self, name: str, delta: int = 1) -> int:
        with self._lock:
            row = self._db.execute("SELECT value FROM counters WHERE name = ?", (name,)).fetchone()
            value = (row["value"] if row else 0) + delta
            self._db.execute(
                "INSERT INTO counters(name, value) VALUES(?, ?) "
                "ON CONFLICT(name) DO UPDATE SET value = excluded.value",
                (name, value),
            )
            self._db.commit()
            return value

    def get_counter(self, name: str) -> int:
        with self._lock:
            row = self._db.execute("SELECT value FROM counters WHERE name = ?", (name,)).fetchone()
            return row["value"] if row else 0

    # -- store --

    def store(
        self,
        payload: bytes,
        content_type: str,
        source_tool: str | None,
        source_server: str | None = None,
        preview: dict | None = None,
    ) -> StoredResult:
        size = len(payload)
        if size > self._settings.max_result_bytes:
            raise StorageLimitError(
                f"Result is {size} bytes, exceeding the "
                f"{self._settings.max_result_bytes}-byte limit."
            )
        with self._lock:
            self._cleanup_expired_locked()

            sha256 = hashlib.sha256(payload).hexdigest()
            blob_path = self._blob_path(sha256)
            blob_row = self._db.execute(
                "SELECT ref_count FROM blobs WHERE sha256 = ?", (sha256,)
            ).fetchone()
            # Duplicate payloads share the blob and must not count against the
            # store quota twice.
            if (
                blob_row is None
                and self._store_size_locked() + size > self._settings.max_store_bytes
            ):
                raise StorageLimitError(
                    "ToolPipe storage limit reached. Release unused results or "
                    "increase results.max_store_bytes."
                )

            # Repair a missing blob file even when the row still exists.
            wrote_file = not blob_path.exists()
            if wrote_file:
                atomic.write_bytes_atomic(blob_path, payload)
            try:
                if blob_row is None:
                    self._db.execute(
                        "INSERT INTO blobs(sha256, path, size_bytes, ref_count, created_at)"
                        " VALUES(?, ?, ?, 1, ?)",
                        (sha256, str(blob_path), size, now_iso()),
                    )
                else:
                    self._db.execute(
                        "UPDATE blobs SET ref_count = ref_count + 1 WHERE sha256 = ?",
                        (sha256,),
                    )
                preview_json = json.dumps(preview) if preview is not None else None
                ref = self._insert_result_locked(
                    sha256,
                    blob_path,
                    content_type,
                    size,
                    source_tool,
                    source_server,
                    preview_json,
                )
                self._db.commit()
            except BaseException:
                self._db.rollback()
                if blob_row is None and wrote_file:
                    blob_path.unlink(missing_ok=True)
                raise
            row = self._db.execute("SELECT * FROM results WHERE ref = ?", (ref,)).fetchone()
            return self._row_to_stored(row)

    def _insert_result_locked(
        self,
        sha256: str,
        blob_path: Path,
        content_type: str,
        size: int,
        source_tool: str | None,
        source_server: str | None,
        preview_json: str | None,
        max_ref_attempts: int = 3,
    ) -> str:
        """Insert the result row, retrying on (vanishingly rare) ref collision."""
        created = now_iso()
        expires_at = expires_iso(self._settings.ttl_seconds)
        for _ in range(max_ref_attempts):
            ref = generate_ref()
            try:
                self._db.execute(
                    "INSERT INTO results(ref, blob_sha256, blob_path, content_type,"
                    " size_bytes, source_tool, source_server, created_at, expires_at,"
                    " preview_json) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        ref,
                        sha256,
                        str(blob_path),
                        content_type,
                        size,
                        source_tool,
                        source_server,
                        created,
                        expires_at,
                        preview_json,
                    ),
                )
                return ref
            except sqlite3.IntegrityError:
                continue
        raise StorageLimitError("Could not allocate a unique result reference.")

    # -- access --

    def get(self, ref: str) -> StoredResult:
        with self._lock:
            return self._get_locked(ref)

    def _get_locked(self, ref: str) -> StoredResult:
        row = self._db.execute("SELECT * FROM results WHERE ref = ?", (ref,)).fetchone()
        if row is None:
            raise ResultNotFoundError(f"Result `{ref}` was not found or has expired.")
        if parse_iso(row["expires_at"]) <= utc_now():
            self._delete_locked(ref)
            self._db.commit()
            raise ResultExpiredError(f"Result `{ref}` was not found or has expired.")
        now = now_iso()
        self._db.execute(
            "UPDATE results SET last_accessed_at = ?, access_count = access_count + 1"
            " WHERE ref = ?",
            (now, ref),
        )
        self._db.commit()
        row = self._db.execute("SELECT * FROM results WHERE ref = ?", (ref,)).fetchone()
        return self._row_to_stored(row)

    def read_bytes(self, ref: str) -> bytes:
        return self.read_bytes_for(self.get(ref))

    def read_bytes_for(self, stored: StoredResult) -> bytes:
        """Read a payload for an already-fetched row (single access bump)."""
        try:
            return Path(stored.blob_path).read_bytes()
        except FileNotFoundError:
            raise ResultNotFoundError(
                f"Result `{stored.ref}` was not found or has expired."
            ) from None

    def release(self, ref: str) -> bool:
        with self._lock:
            row = self._db.execute("SELECT * FROM results WHERE ref = ?", (ref,)).fetchone()
            if row is None:
                raise ResultNotFoundError(f"Result `{ref}` was not found or has expired.")
            self._delete_locked(ref)
            self._db.commit()
            return True

    def _delete_locked(self, ref: str) -> None:
        row = self._db.execute("SELECT blob_sha256 FROM results WHERE ref = ?", (ref,)).fetchone()
        if row is None:
            return
        sha256 = row["blob_sha256"]
        self._db.execute("DELETE FROM results WHERE ref = ?", (ref,))
        blob = self._db.execute(
            "SELECT ref_count, path FROM blobs WHERE sha256 = ?", (sha256,)
        ).fetchone()
        if blob is None:
            return
        if blob["ref_count"] <= 1:
            self._db.execute("DELETE FROM blobs WHERE sha256 = ?", (sha256,))
            Path(blob["path"]).unlink(missing_ok=True)
        else:
            self._db.execute(
                "UPDATE blobs SET ref_count = ref_count - 1 WHERE sha256 = ?", (sha256,)
            )

    # -- cleanup / accounting --

    def cleanup_expired(self) -> int:
        with self._lock:
            return self._cleanup_expired_locked(commit=True)

    def _cleanup_expired_locked(self, commit: bool = False) -> int:
        rows = self._db.execute("SELECT ref, expires_at FROM results").fetchall()
        now = utc_now()
        count = 0
        for row in rows:
            if parse_iso(row["expires_at"]) <= now:
                self._delete_locked(row["ref"])
                count += 1
        if commit or count:
            self._db.commit()
        return count

    def _store_size_locked(self) -> int:
        row = self._db.execute("SELECT COALESCE(SUM(size_bytes), 0) AS total FROM blobs").fetchone()
        return row["total"]

    def store_size_bytes(self) -> int:
        with self._lock:
            return self._store_size_locked()

    def active_count(self) -> int:
        with self._lock:
            row = self._db.execute("SELECT COUNT(*) AS n FROM results").fetchone()
            return row["n"]

    def list_all(self) -> list[StoredResult]:
        """List all result rows without touching access metadata."""
        with self._lock:
            rows = self._db.execute("SELECT * FROM results ORDER BY created_at").fetchall()
            return [self._row_to_stored(r) for r in rows]

    @staticmethod
    def _row_to_stored(row: sqlite3.Row) -> StoredResult:
        return StoredResult(
            ref=row["ref"],
            blob_sha256=row["blob_sha256"],
            blob_path=row["blob_path"],
            content_type=row["content_type"],
            size_bytes=row["size_bytes"],
            source_tool=row["source_tool"],
            source_server=row["source_server"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            last_accessed_at=row["last_accessed_at"],
            access_count=row["access_count"],
            preview_json=row["preview_json"],
        )
