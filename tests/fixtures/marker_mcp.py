"""Downstream server that marks its own startup (lazy-spawn probe)."""

import os

marker = os.environ.get("TP_MARKER_FILE", "")
if marker:
    with open(marker, "w") as f:
        f.write("started")

from fastmcp import FastMCP

mcp = FastMCP("Marker")


@mcp.tool
def ping() -> str:
    return "pong"


if __name__ == "__main__":
    mcp.run()
