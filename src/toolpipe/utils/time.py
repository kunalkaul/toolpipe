"""UTC timestamps as ISO-8601 strings."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def now_iso() -> str:
    return to_iso(utc_now())


def expires_iso(ttl_seconds: int) -> str:
    return to_iso(utc_now() + timedelta(seconds=ttl_seconds))


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)
