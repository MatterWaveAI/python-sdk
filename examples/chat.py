# /// script
# requires-python = ">=3.10"
# dependencies = ["matterwave-sdk>=0.5"]
# ///
"""One chat completion: a prompt in, the answer and its token count out.

    MATTERWAVE_BASE_URL=https://<your-matterwave> MATTERWAVE_API_KEY=mw_... \\
        uv run examples/chat.py "Say hello in five words."
"""

from __future__ import annotations

import os
import sys

from matterwave_sdk import APIError, MatterWave


def run(client: MatterWave, prompt: str) -> int:
    try:
        completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
        )
    except APIError as err:
        print(f"failed: {err.status_code} {err.code} {err.request_id}", file=sys.stderr)
        return 1
    print(completion.content)
    print(f"[{completion.model}: {completion.usage.total_tokens} tokens]")
    return 0


def main() -> int:
    if not os.environ.get("MATTERWAVE_API_KEY"):
        print("Set MATTERWAVE_API_KEY to a MatterWave API key.", file=sys.stderr)
        return 2
    with MatterWave() as client:  # MATTERWAVE_API_KEY and MATTERWAVE_BASE_URL
        return run(client, " ".join(sys.argv[1:]) or "Say hello in five words.")


if __name__ == "__main__":
    sys.exit(main())
