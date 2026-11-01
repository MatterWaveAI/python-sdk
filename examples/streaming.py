# /// script
# requires-python = ">=3.10"
# dependencies = ["matterwave-sdk>=0.5"]
# ///
"""Stream an answer as it is written, then report the whole completion.

    MATTERWAVE_BASE_URL=https://<your-matterwave> MATTERWAVE_API_KEY=mw_... \\
        uv run examples/streaming.py "Write a haiku about rivers."

Each chunk's text is printed as it arrives and fed to a
`ChatCompletionAccumulator`, which ends up holding what a non-streamed call
would have returned: the message, the finish reason and the usage (the
stream carries the usage because the call asks for it). A caller that only
wants the whole answer can skip the loop: `stream.final_completion()`.
"""

from __future__ import annotations

import os
import sys

from matterwave_sdk import APIError, ChatCompletionAccumulator, MatterWave


def run(client: MatterWave, prompt: str) -> int:
    accumulator = ChatCompletionAccumulator()
    try:
        with client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}], stream=True, include_usage=True
        ) as stream:
            for chunk in stream:
                print(chunk.content, end="", flush=True)
                accumulator.add(chunk)
    except APIError as err:
        print(f"\nfailed: {err.status_code} {err.code} {err.request_id}", file=sys.stderr)
        return 1
    completion = accumulator.completion()
    print()
    print(
        f"[{completion.choices[0].finish_reason}: "
        f"{completion.usage.prompt_tokens} in, {completion.usage.completion_tokens} out]"
    )
    return 0


def main() -> int:
    if not os.environ.get("MATTERWAVE_API_KEY"):
        print("Set MATTERWAVE_API_KEY to a MatterWave API key.", file=sys.stderr)
        return 2
    with MatterWave() as client:
        return run(client, " ".join(sys.argv[1:]) or "Write a haiku about rivers.")


if __name__ == "__main__":
    sys.exit(main())
