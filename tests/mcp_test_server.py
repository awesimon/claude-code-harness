from __future__ import annotations

import os
import asyncio

from mcp.server.fastmcp import FastMCP


mcp = FastMCP(
    "python-harness-test",
    host="127.0.0.1",
    port=int(os.environ.get("MCP_TEST_PORT", "8000")),
    json_response=True,
)


@mcp.tool()
def echo(text: str) -> dict[str, str]:
    """Return structured text unchanged."""

    return {"text": text}


@mcp.tool()
def fail(message: str = "failed") -> dict[str, str]:
    """Raise a controlled server-side failure."""

    raise RuntimeError(message)


@mcp.tool()
async def slow(seconds: float) -> dict[str, float]:
    """Wait so clients can exercise timeout and cancellation."""

    await asyncio.sleep(seconds)
    return {"seconds": seconds}


@mcp.resource("test://value", mime_type="text/plain")
def value() -> str:
    return "value"


if __name__ == "__main__":
    mcp.run(os.environ.get("MCP_TEST_TRANSPORT", "stdio"))
