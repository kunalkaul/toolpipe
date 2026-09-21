"""Demo producer MCP: deterministic customer records."""

from fastmcp import FastMCP

mcp = FastMCP("Producer")


@mcp.tool
def generate_customers(count: int) -> dict:
    """Generate deterministic customer records with id, name, and revenue."""
    return {
        "customers": [
            {
                "id": i,
                "name": f"Customer {i}",
                "revenue": float(i % 5000),
            }
            for i in range(count)
        ]
    }


if __name__ == "__main__":
    mcp.run()
