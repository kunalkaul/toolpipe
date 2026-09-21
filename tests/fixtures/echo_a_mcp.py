"""Tiny downstream echo server A (fixture for proxy tests)."""

from fastmcp import FastMCP

mcp = FastMCP("EchoA")


@mcp.tool
def echo(text: str) -> str:
    return f"A:{text}"


@mcp.tool
def boom() -> str:
    raise RuntimeError("A failed")


@mcp.tool
def user_info(name: str) -> dict:
    """Look up a user."""
    return {"name": name, "id": 1}


@mcp.tool
def big_payload(size_kb: int) -> dict:
    """Return a large structured payload."""
    return {"blob": "x" * (size_kb * 1024)}


@mcp.tool
def big_text(size_kb: int) -> str:
    """Return a large text payload."""
    return "y" * (size_kb * 1024)


if __name__ == "__main__":
    mcp.run()
