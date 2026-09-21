"""Tiny downstream echo server B (fixture for proxy tests)."""

from fastmcp import FastMCP

mcp = FastMCP("EchoB")


@mcp.tool
def echo(text: str) -> str:
    return f"B:{text}"


if __name__ == "__main__":
    mcp.run()
