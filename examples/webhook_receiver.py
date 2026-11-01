# /// script
# requires-python = ">=3.10"
# dependencies = ["matterwave-sdk>=0.5"]
# ///
"""A webhook receiver: verify each delivery with `webhooks.unwrap`, then act.

    MATTERWAVE_WEBHOOK_SECRET=whsec_... uv run examples/webhook_receiver.py 8080

Point an endpoint (`client.webhooks.create(url, events)`) at this server
through an https tunnel or proxy; the secret is the one `create` returned.
`unwrap` checks the delivery's signature -- the `MatterWave-Signature`
header or the Standard Webhooks ones -- against the raw body before parsing
it, and refuses a stale or forged one, which this server answers 400.
Delivery is at least once: dedupe on the event's `id`.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, HTTPServer

from matterwave_sdk import WebhookVerificationError, webhooks

SEEN: set[str] = set()


def handle_delivery(body: bytes, headers: Mapping[str, str], secret: str) -> tuple[int, str]:
    """The status to answer and a line for the log."""
    try:
        event = webhooks.unwrap(body, headers, secret)
    except WebhookVerificationError as err:
        return 400, f"refused: {err}"
    event_id = str(event.get("id"))
    if event_id in SEEN:
        return 204, f"{event_id}: already handled"
    SEEN.add(event_id)
    return 204, f"{event_id}: {event.get('type')} {event.get('data')}"


def serve(port: int, secret: str) -> None:
    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - the name http.server calls
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            status, line = handle_delivery(body, dict(self.headers.items()), secret)
            print(line, flush=True)
            self.send_response(status)
            self.end_headers()

    print(f"listening on :{port}", flush=True)
    HTTPServer(("", port), Receiver).serve_forever()


def main() -> int:
    secret = os.environ.get("MATTERWAVE_WEBHOOK_SECRET")
    if not secret:
        print("Set MATTERWAVE_WEBHOOK_SECRET to the endpoint's signing secret.", file=sys.stderr)
        return 2
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8080, secret)
    return 0


if __name__ == "__main__":
    sys.exit(main())
