# /// script
# requires-python = ">=3.10"
# dependencies = ["matterwave-sdk>=0.5"]
# ///
"""Sign in from a terminal by device code, either of two ways.

    MATTERWAVE_BASE_URL=https://<your-matterwave> uv run examples/device_sign_in.py key
    MATTERWAVE_BASE_URL=https://<your-matterwave> uv run examples/device_sign_in.py person

`key` is the API's own device flow: the person approves the code in a
browser where they are signed in, and the poll returns an API key (with
`chat:read` and `chat:write`) that acts as them. `person` signs them in at
the deployment's identity provider instead and returns their own token,
which the routes that manage the organisation require; `session(token)`
keeps it fresh. Either way, store the credential in the OS keyring, never a
dotfile; this example only prints who it acts as.
"""

from __future__ import annotations

import sys

from matterwave_sdk import APIError, DeviceAuthError, DeviceCode, MatterWave, MatterWaveError


def show(code: DeviceCode) -> None:
    print(f"Open {code.verification_uri} and enter {code.user_code}", flush=True)


def run(client: MatterWave, mode: str, *, timeout: float = 600) -> int:
    try:
        if mode == "key":
            token = client.auth.device.login(on_code=show, timeout=timeout, client_name="example")
            signed_in = MatterWave(api_key=token.access_token, base_url=client.base_url)
        else:
            person = client.auth.person
            session = person.session(person.login(on_code=show, timeout=timeout))
            signed_in = MatterWave(access_token=session, base_url=client.base_url)
        with signed_in:
            me = signed_in.me()
    except DeviceAuthError as err:
        print(f"not signed in: {err.code}", file=sys.stderr)
        return 1
    except APIError as err:
        print(f"failed: {err.status_code} {err.code} {err.request_id}", file=sys.stderr)
        return 1
    except MatterWaveError as err:
        print(f"failed: {err}", file=sys.stderr)
        return 1
    print(f"signed in as {me.email or me.subject} ({me.org_role or 'no org role'})")
    return 0


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "key"
    if mode not in ("key", "person"):
        print("usage: device_sign_in.py [key|person]", file=sys.stderr)
        return 2
    with MatterWave() as client:  # MATTERWAVE_BASE_URL; no credential needed to sign in
        return run(client, mode)


if __name__ == "__main__":
    sys.exit(main())
