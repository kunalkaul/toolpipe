"""Tiny analytics server (fixture for pipe tests)."""

from fastmcp import FastMCP

mcp = FastMCP("Analytics")


@mcp.tool
def calculate_statistics(values: list[float], round_to: int = 2) -> dict:
    """Calculate count/min/max/mean over a list of values."""
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "mean": round(sum(values) / len(values), round_to),
    }


@mcp.tool
def echo_records(records: dict) -> dict:
    """Echo back the keys of a record mapping."""
    return {"keys": sorted(records.keys())}


@mcp.tool
def echo_text(data: str) -> dict:
    """Echo back the length of a text payload."""
    return {"length": len(data)}


@mcp.tool
def big_echo(values: list[float]) -> dict:
    """Echo values back wrapped in a large payload."""
    return {"values": values, "pad": "q" * 200000}


if __name__ == "__main__":
    mcp.run()
