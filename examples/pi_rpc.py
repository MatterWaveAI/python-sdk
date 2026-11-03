# /// script
# requires-python = ">=3.10"
# dependencies = ["matterwave-sdk>=0.5"]
# ///
"""Drive Pi 1.0 (`pi --mode rpc`) with `client.pi`: build the subprocess
environment and provider config, then run one prompt over JSONL RPC.

    MATTERWAVE_BASE_URL=https://<your-matterwave> MATTERWAVE_API_KEY=mw_... \\
        uv run examples/pi_rpc.py "Summarize src/main.py"
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

from matterwave_sdk import MatterWave, MatterWaveError


def run(
    client: MatterWave,
    prompt: str,
    *,
    command: Sequence[str] | None = None,
    preset_id: str | None = None,
    temporary: bool = False,
) -> int:
    try:
        env = client.pi.build_env(preset_id=preset_id, temporary=temporary)
        provider = client.pi.build_provider_config(preset_id=preset_id, temporary=temporary)
        models = [m["id"] for m in provider["models"]]
        print(f"[provider: {provider['baseUrl']} ({len(models)} models)]")
        with client.pi.create_rpc_session(
            command=command,
            env=env,
            preset_id=preset_id,
            temporary=temporary,
        ) as session:
            state = session.get_state()
            for event in session.prompt(prompt):
                if event.delta:
                    print(event.delta, end="", flush=True)
            print()
            print(
                f"[session {state.get('sessionId', 'unknown')}: {state.get('modelId', models[0])}]"
            )
    except MatterWaveError as err:
        print(f"failed: {err}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    if not os.environ.get("MATTERWAVE_API_KEY"):
        print("Set MATTERWAVE_API_KEY to a MatterWave API key.", file=sys.stderr)
        return 2
    with MatterWave() as client:
        return run(client, " ".join(sys.argv[1:]) or "Summarize this project.")


if __name__ == "__main__":
    sys.exit(main())
