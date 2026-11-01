"""Vendor the platform's whole route document, pinned to one platform commit.

    python scripts/refresh_openapi_full.py ../matterwave-app
    python scripts/refresh_openapi_full.py ../matterwave-app --ref <branch-or-sha>
    python scripts/refresh_openapi_full.py ../matterwave-app --check

The source is a checkout of the platform repo. At `--ref` (its HEAD when
omitted) this reads two files with `git show`, so the checkout's working tree
is never consulted:

* `docs/developers/openapi-full.json`, every route a person or a script may
  call with the credentials each accepts, written byte for byte to
  tests/openapi-full.json;
* `scripts/export_openapi.py`, whose `INTERNAL_PREFIXES` and `INTERNAL_ROUTES`
  name the routes the platform serves but leaves out of that document (the
  operator console, the edge mirror, the browser's sign-in forms), read as
  literals and written with the commit and the document's SHA-256 to
  tests/openapi-full.pin.json.

Together they are what the route drift tests hold the SDK to: every route
the SDK calls is in the document or named internal, every route in the
document is wrapped or listed as not covered, and a deployment's live
`/openapi.json` is exactly the document plus the internal routes.
`--check` writes nothing and exits 1 when the vendored pair differs from the
ref, which is how a refresh is proven unnecessary.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

TESTS = Path(__file__).resolve().parents[1] / "tests"
DOCUMENT = TESTS / "openapi-full.json"
PIN = TESTS / "openapi-full.pin.json"
DOCUMENT_SOURCE = "docs/developers/openapi-full.json"
EXPORTER_SOURCE = "scripts/export_openapi.py"


def git(checkout: Path, *args: str) -> bytes:
    result = subprocess.run(  # noqa: S603 - fixed git subcommands on a path the caller named
        ["git", "-C", str(checkout), *args],  # noqa: S607 - git from PATH, as a developer runs it
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr.decode("utf-8", "replace").strip() or "git failed")
    return result.stdout


def internal_routes(exporter: str) -> tuple[dict[str, str], list[list[str]]]:
    """The exporter's two literals: prefix -> reason, and (method, path) ->
    reason, as sorted lists so the pin diffs cleanly."""
    found: dict[str, Any] = {}
    for node in ast.parse(exporter).body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id in ("INTERNAL_PREFIXES", "INTERNAL_ROUTES")
            and node.value is not None
        ):
            found[node.target.id] = ast.literal_eval(node.value)
    missing = {"INTERNAL_PREFIXES", "INTERNAL_ROUTES"} - set(found)
    if missing:
        raise SystemExit(f"{EXPORTER_SOURCE} has no {', '.join(sorted(missing))}")
    prefixes = dict(sorted(found["INTERNAL_PREFIXES"].items()))
    routes = sorted(
        [method, path, reason] for (method, path), reason in found["INTERNAL_ROUTES"].items()
    )
    return prefixes, routes


def validate(document: bytes) -> None:
    try:
        parsed = json.loads(document)
    except ValueError as exc:
        raise SystemExit(f"{DOCUMENT_SOURCE} is not JSON: {exc}") from exc
    schemes = parsed.get("components", {}).get("securitySchemes", {})
    if not parsed.get("paths") or "apiKey" not in schemes:
        raise SystemExit(f"{DOCUMENT_SOURCE} has no paths or no apiKey scheme")


def render_pin(revision: str, document: bytes, exporter: str) -> bytes:
    prefixes, routes = internal_routes(exporter)
    pin = {
        "platform_revision": revision,
        "source": DOCUMENT_SOURCE,
        "sha256": hashlib.sha256(document).hexdigest(),
        "internal_prefixes": prefixes,
        "internal_routes": routes,
    }
    return (json.dumps(pin, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("checkout", type=Path, help="a checkout of the platform repo")
    parser.add_argument("--ref", default="HEAD", help="the platform commit or branch to pin")
    parser.add_argument(
        "--check", action="store_true", help="write nothing; exit 1 if the vendored pair differs"
    )
    args = parser.parse_args(argv)
    revision = git(args.checkout, "rev-parse", "--verify", f"{args.ref}^{{commit}}").decode()
    revision = revision.strip()
    document = git(args.checkout, "show", f"{revision}:{DOCUMENT_SOURCE}")
    validate(document)
    exporter = git(args.checkout, "show", f"{revision}:{EXPORTER_SOURCE}").decode("utf-8")
    pin = render_pin(revision, document, exporter)
    current = (
        DOCUMENT.read_bytes() if DOCUMENT.exists() else b"",
        PIN.read_bytes() if PIN.exists() else b"",
    )
    # The revision alone changing is not drift: the same document at a later
    # commit pins the same routes.
    same = current[0] == document and _routes_of(current[1]) == _routes_of(pin)
    if args.check:
        if not same:
            print(f"the vendored route document differs from {revision}; run without --check")
            return 1
        print(f"the vendored route document matches {revision}")
        return 0
    if same:
        print(f"the vendored route document already matches {revision}")
        return 0
    DOCUMENT.write_bytes(document)
    PIN.write_bytes(pin)
    print(f"vendored {DOCUMENT_SOURCE} and its internal routes at {revision}")
    return 0


def _routes_of(pin: bytes) -> Any:
    if not pin:
        return None
    parsed = json.loads(pin)
    parsed.pop("platform_revision", None)
    return parsed


if __name__ == "__main__":
    sys.exit(main())
