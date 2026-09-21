"""Shared constants."""

CONTROL_TAG = "toolpipe-control"
RESERVED_NAMESPACE = "toolpipe"
REF_PREFIX = "res_"

INSTRUCTIONS = (
    "Large downstream results may be returned as ToolPipe result references. "
    "Use toolpipe_inspect_result, toolpipe_select_result, or "
    "toolpipe_pipe_result instead of requesting the full result."
)
