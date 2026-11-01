# /// script
# requires-python = ">=3.10"
# dependencies = ["matterwave-sdk>=0.5"]
# ///
"""Walk cursor-paged lists without handling a cursor.

    MATTERWAVE_BASE_URL=https://<your-matterwave> MATTERWAVE_API_KEY=mw_... \\
        uv run examples/pagination.py "invoice"

`items()` (and `search_items`) fetch the next page only when the loop
reaches it, so stopping early fetches nothing more; `pages()` hands over
whole pages instead. `pages_of` / `items_of` do the same walk for any list
that answers `{"data": [...], "next_cursor": ...}`, given how to fetch one
page from a cursor.
"""

from __future__ import annotations

import itertools
import os
import sys

from matterwave_sdk import APIError, MatterWave, items_of, pages_of


def run(client: MatterWave, query: str) -> int:
    try:
        archived = sum(1 for _ in client.conversations.items(filter="archived", limit=100))
        print(f"archived conversations: {archived}")

        for hit in itertools.islice(client.library.search_items(query, limit=20), 5):
            print(f"match: {hit.conversation.title}")

        pinned = items_of(
            pages_of(lambda cursor: client.conversations.list(filter="pinned", cursor=cursor))
        )
        print("pinned:", ", ".join(c.title for c in pinned) or "none")
    except APIError as err:
        print(f"failed: {err.status_code} {err.code} {err.request_id}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    if not os.environ.get("MATTERWAVE_API_KEY"):
        print("Set MATTERWAVE_API_KEY to a MatterWave API key.", file=sys.stderr)
        return 2
    with MatterWave() as client:
        return run(client, " ".join(sys.argv[1:]) or "invoice")


if __name__ == "__main__":
    sys.exit(main())
