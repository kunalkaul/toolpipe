"""Configuration and result-store settings models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ResultSettings:
    storage_dir: str = ".toolpipe"
    inline_max_bytes: int = 32768
    preview_max_bytes: int = 4096
    read_max_bytes: int = 16384
    max_result_bytes: int = 104857600
    max_store_bytes: int = 1073741824
    ttl_seconds: int = 3600


@dataclass(frozen=True)
class ServerConfig:
    name: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    url: str | None = None
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ApprovalRecord:
    """A persisted or runtime grant for a downstream server."""

    name: str
    scope: str  # once | session | workspace | always | config
    command: str | None = None
    args: list[str] = field(default_factory=list)
    url: str | None = None
    env: dict[str, str] = field(default_factory=dict)

    def to_server_config(self) -> ServerConfig:
        return ServerConfig(
            name=self.name,
            command=self.command,
            args=list(self.args),
            url=self.url,
            env=dict(self.env),
        )


CONSENT_SCOPES = ("once", "session", "workspace", "always")
POLICY_MODES = ("auto", "always", "never")


@dataclass(frozen=True)
class ToolPipeConfig:
    name: str = "ToolPipe"
    results: ResultSettings = field(default_factory=ResultSettings)
    servers: dict[str, ServerConfig] = field(default_factory=dict)


@dataclass(frozen=True)
class StoredResult:
    ref: str
    blob_sha256: str
    blob_path: str
    content_type: str
    size_bytes: int
    source_tool: str | None
    source_server: str | None
    created_at: str
    expires_at: str
    last_accessed_at: str | None
    access_count: int
    preview_json: str | None = None


@dataclass(frozen=True)
class ReadResult:
    ref: str
    offset: int
    returned_bytes: int
    has_more: bool
    content: str
    content_type: str


@dataclass(frozen=True)
class ResultInfo:
    ref: str
    content_type: str
    size_bytes: int
    source_tool: str | None
    created_at: str
    expires_at: str
    preview_json: str | None = None


@dataclass(frozen=True)
class SelectionResult:
    ref: str
    virtualized: bool
    content_type: str
    size_bytes: int
    value: object = None
