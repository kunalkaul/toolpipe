"""Atomic filesystem writes."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_bytes_atomic(path: str | Path, data: bytes) -> None:
    """Write bytes to path atomically via tmp file + fsync + os.replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
