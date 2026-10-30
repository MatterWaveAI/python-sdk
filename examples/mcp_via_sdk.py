# /// script
# requires-python = ">=3.10"
# dependencies = ["matterwave-sdk>=0.3"]
# ///
"""Drive a MatterWave deployment through the SDK's own MCP wrapper, `client.mcp`.

    MATTERWAVE_BASE_URL=https://<your-matterwave> MATTERWAVE_API_KEY=mw_... \\
        uv run examples/mcp_via_sdk.py "pumpkin soup"

`/mcp` is the conversation API in a second wire format: the wrapper lists
the tools the key's scopes allow, starts a conversation, sends one message
and waits out the whole model turn (ten minutes by default, not the
client's 60 seconds), then searches for it. To connect an *editor*
instead -- Claude Desktop, VS Code, Cursor -- see docs/mcp.md: editors
speak the protocol themselves and need only the URL and the key.
"""

from __future__ import annotations

import os
import sys

from matterwave_sdk import APIError, MatterWave, McpToolError


def main() -> int:
    base_url = os.environ.get("MATTERWAVE_BASE_URL", "http://localhost:8000").rstrip("/")
    api_key = os.environ.get("MATTERWAVE_API_KEY")
    if not api_key:
        print("Set MATTERWAVE_API_KEY to a MatterWave API key.", file=sys.stderr)
        return 2
    question = " ".join(sys.argv[1:]) or "hello"
    with MatterWave(api_key=api_key, base_url=base_url) as client:
        try:
            tools = client.mcp.list_tools()
            print("tools:", ", ".join(tool.name for tool in tools))

            created = client.mcp.call_tool(
                "create_conversation", {"title": "From the MCP example"}
            )
            conversation_id = created.structured["conversation"]["id"]

            answer = client.mcp.send_message(
                conversation_id, f"Answer in one sentence: {question}"
            )
            print("answer:", answer.text)

            found = client.mcp.call_tool(
                "search_conversations", {"query": question, "limit": 5}
            )
            print("searched:", found.structured["query"])
            return 0
        except McpToolError as err:
            # A tool that ran and refused: the budget, the plan, moderation.
            print(f"refused: {err.error_code}: {err.detail}", file=sys.stderr)
            return 1
        except APIError as err:
            # A refusal before any method ran: auth, the key's rate limit.
            print(f"failed: {err.status_code} {err.code} {err.request_id}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    sys.exit(main())
