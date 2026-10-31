"""Refresh tests/openapi.json, the snapshot the contract tests read, from the
app's exported OpenAPI document.

    python scripts/refresh_openapi.py ../matterwave-app/docs/developers/openapi.json
    python scripts/refresh_openapi.py https://<host>/<path>/openapi.json
    python scripts/refresh_openapi.py --check <path-or-url>

The source is a file path or an http(s) URL. Its bytes are written as they
are, so the snapshot diffs line for line against the app's copy, after a
check that it is an OpenAPI document with paths and the `x-sse-events` the
frame tests read. `--check` writes nothing and exits 1 when the snapshot
differs from the source, which is how CI asks "is this SDK behind the API?".
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parents[1] / "tests" / "openapi.json"


def fetch(source: str) -> bytes:
    if source.startswith(("http://", "https://")):
        # The scheme is checked just above, so urlopen cannot be handed a
        # file: or ftp: URL from the command line.
        with urllib.request.urlopen(source, timeout=30) as response:  # noqa: S310
            body: bytes = response.read()
            return body
    return Path(source).read_bytes()


def validate(body: bytes) -> None:
    try:
        document = json.loads(body)
    except ValueError as exc:
        raise SystemExit(f"not JSON: {exc}") from exc
    if not isinstance(document, dict) or "openapi" not in document:
        raise SystemExit("not an OpenAPI document: no top-level `openapi` key")
    for key in ("paths", "components", "x-sse-events"):
        if key not in document:
            raise SystemExit(f"the document has no `{key}`; is this the app's export?")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", help="path or http(s) URL of docs/developers/openapi.json")
    parser.add_argument(
        "--check", action="store_true", help="write nothing; exit 1 if the snapshot differs"
    )
    args = parser.parse_args(argv)
    body = fetch(args.source)
    validate(body)
    current = SNAPSHOT.read_bytes() if SNAPSHOT.exists() else b""
    if args.check:
        if current != body:
            print(f"{SNAPSHOT.name} differs from {args.source}; run without --check to refresh")
            return 1
        print(f"{SNAPSHOT.name} matches {args.source}")
        return 0
    if current == body:
        print(f"{SNAPSHOT.name} already matches {args.source}")
        return 0
    SNAPSHOT.write_bytes(body)
    print(f"{SNAPSHOT.name} refreshed from {args.source}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
