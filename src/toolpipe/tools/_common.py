"""Shared helpers for ToolPipe control tools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, TypeVar

from fastmcp.exceptions import ToolError

from toolpipe.errors import ToolPipeError

T = TypeVar("T")


def translate_errors(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    """Map internal ToolPipe errors to concise MCP ToolErrors."""

    @wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        try:
            return await fn(*args, **kwargs)
        except ToolPipeError as e:
            raise ToolError(str(e)) from e

    return wrapper
