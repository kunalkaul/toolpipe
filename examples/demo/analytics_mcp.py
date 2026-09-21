"""Demo analytics MCP: statistics over piped values."""

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


if __name__ == "__main__":
    mcp.run()
