"""JMESPath-only selection. No arbitrary code."""

from __future__ import annotations

from typing import Any

import jmespath
import jmespath.exceptions

from toolpipe.errors import SelectionError


def evaluate(value: Any, expression: str) -> Any:
    """Evaluate a JMESPath expression against a parsed JSON value."""
    try:
        compiled = jmespath.compile(expression)
    except jmespath.exceptions.JMESPathError as e:
        raise SelectionError(f"JMESPath expression is invalid: {expression}: {e}")
    try:
        return compiled.search(value)
    except jmespath.exceptions.JMESPathError as e:
        raise SelectionError(f"JMESPath evaluation failed for `{expression}`: {e}")
