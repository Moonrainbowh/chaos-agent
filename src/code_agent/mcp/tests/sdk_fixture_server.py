from mcp.server.fastmcp import FastMCP


server = FastMCP("chaos-mcp-fixture")


@server.tool()
def read_text(path: str) -> str:
    """Return a bounded fixture result without accessing the filesystem."""
    return f"fixture:{path[:64]}"


if __name__ == "__main__":
    server.run(transport="stdio")
