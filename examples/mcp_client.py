# /// script
# requires-python = ">=3.12"
# dependencies = ["mcp>=2.3,<3", "httpx2"]
# ///
"""Connect to a MatterWave deployment as an MCP client and search your conversations.

    MATTERWAVE_BASE_URL=https://<your-matterwave> MATTERWAVE_API_KEY=mw_... \\
        uv run sdk/python/examples/mcp_client.py "pumpkin soup"

The official `mcp` package does the protocol work: it discovers the server's
revision, lists the tools and calls one. The only MatterWave-specific part is
the bearer header, which is an ordinary API key (Settings -> API keys).
"""

from __future__ import annotations

import asyncio
import os
import sys

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


async def main(query: str) -> int:
    base_url = os.environ.get("MATTERWAVE_BASE_URL", "http://localhost:8000").rstrip("/")
    api_key = os.environ.get("MATTERWAVE_API_KEY")
    if not api_key:
        print("Set MATTERWAVE_API_KEY to a MatterWave API key.", file=sys.stderr)
        return 2
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {api_key}"}, timeout=60)
    try:
        async with Client(streamable_http_client(f"{base_url}/mcp", http_client=http)) as client:
            info = client.server_info
            print(f"connected: {info.name if info else '?'} speaking {client.protocol_version}")
            tools = await client.list_tools()
            print("tools:", ", ".join(tool.name for tool in tools.tools))
            result = await client.call_tool("search_conversations", {"query": query})
            if result.is_error:
                print("refused:", result.structured_content, file=sys.stderr)
                return 1
            for block in result.content:
                if block.type == "text":
                    print(block.text)
    finally:
        await http.aclose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(" ".join(sys.argv[1:]) or "hello")))
