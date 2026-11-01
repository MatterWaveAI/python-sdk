"""Hold a running deployment's routes to the SDK and the vendored document.

    python scripts/check_live_routes.py https://<host>/openapi.json
    python scripts/check_live_routes.py saved-openapi.json

The source is the deployment's own `/openapi.json` (every route the app
serves) or a saved copy of it. Drift is any of:

* a route the SDK calls that the deployment does not serve as called;
* a route in tests/openapi-full.json that the deployment no longer serves;
* a route the deployment serves that is neither in tests/openapi-full.json
  nor one the pin in tests/openapi-full.pin.json names internal -- a route
  added since the pin, which a refresh would then classify.

The report is Markdown on stdout, and the exit status is 1 on drift, so the
scheduled workflow can open an issue with it. Point SSL_CERT_FILE at the
deployment's CA when its certificate is not publicly trusted.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

from matterwave_sdk import APP_OPERATIONS, OPERATIONS

TESTS = Path(__file__).resolve().parents[1] / "tests"
METHODS = ("get", "post", "put", "patch", "delete")


def fetch(source: str) -> dict[str, Any]:
    if source.startswith(("http://", "https://")):
        # The scheme is checked just above, so urlopen cannot be handed a
        # file: or ftp: URL from the command line.
        with urllib.request.urlopen(source, timeout=30) as response:  # noqa: S310
            body: bytes = response.read()
    else:
        body = Path(source).read_bytes()
    document = json.loads(body)
    if not isinstance(document, dict) or not isinstance(document.get("paths"), dict):
        raise SystemExit(f"{source} is not an OpenAPI document with paths")
    return document


def routes_of(document: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (method.upper(), path)
        for path, item in document["paths"].items()
        for method in item
        if method in METHODS
    }


def internal(pin: dict[str, Any], method: str, path: str) -> bool:
    if [method, path] in [route[:2] for route in pin["internal_routes"]]:
        return True
    return any(path.startswith(prefix) for prefix in pin["internal_prefixes"])


def drift(live: set[tuple[str, str]]) -> dict[str, list[str]]:
    vendored = routes_of(json.loads((TESTS / "openapi-full.json").read_bytes()))
    pin = json.loads((TESTS / "openapi-full.pin.json").read_text(encoding="utf-8"))
    sdk = {name: (op["method"], op["path"]) for name, op in OPERATIONS.items()}
    sdk.update(APP_OPERATIONS)
    return {
        "Routes the SDK calls that the deployment does not serve": sorted(
            f"`{method} {path}` ({name})"
            for name, (method, path) in sdk.items()
            if (method, path) not in live
        ),
        f"Routes in the pinned document ({pin['platform_revision'][:12]}) the deployment no longer serves": sorted(
            f"`{method} {path}`" for method, path in vendored - live
        ),
        "Routes the deployment serves that the pinned document does not know": sorted(
            f"`{method} {path}`"
            for method, path in live - vendored
            if not internal(pin, method, path)
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", help="the deployment's /openapi.json, as a URL or a saved file")
    args = parser.parse_args(argv)
    found = drift(routes_of(fetch(args.source)))
    if not any(found.values()):
        print(f"No route drift against {args.source}.")
        return 0
    print(f"# Route drift against {args.source}\n")
    for heading, lines in found.items():
        if lines:
            print(f"## {heading}\n")
            print("\n".join(f"- {line}" for line in lines) + "\n")
    print(
        "Refresh the pin with `scripts/refresh_openapi_full.py` from the platform commit the "
        "deployment runs, then wrap each new route or list it in tests/routes-not-covered.json."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
