"""Check a built API reference covers the whole public surface.

    uv run --with pdoc==15.0.4 pdoc -o reference matterwave_sdk
    uv run python scripts/check_reference.py reference

pdoc writes one page per public module (`matterwave_sdk.html`, and
`matterwave_sdk/agent.html`, `frames.html`, `webhooks.html`) with an anchor per
documented member. Every name in a module's `__all__` (or, without one, every
class and function it defines) must have its anchor on that module's page, so
a symbol pdoc could not render, or a module the build skipped, fails here
rather than going missing from the published reference. Exits 1 and lists what
is absent.
"""

from __future__ import annotations

import importlib
import inspect
import re
import sys
from pathlib import Path

MODULES = {
    "matterwave_sdk": "matterwave_sdk.html",
    "matterwave_sdk.agent": "matterwave_sdk/agent.html",
    "matterwave_sdk.frames": "matterwave_sdk/frames.html",
    "matterwave_sdk.webhooks": "matterwave_sdk/webhooks.html",
}


def public_names(module_name: str) -> list[str]:
    """`__all__`, or for a module without one the classes and functions it
    defines itself; a submodule is its own page, not a member."""
    module = importlib.import_module(module_name)
    names = getattr(module, "__all__", None)
    if names is None:
        names = [
            name
            for name, value in vars(module).items()
            if not name.startswith("_")
            and (inspect.isclass(value) or inspect.isfunction(value))
            and value.__module__ == module_name
        ]
    return [name for name in names if not inspect.ismodule(getattr(module, name))]


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: check_reference.py <pdoc output directory>", file=sys.stderr)
        return 2
    root = Path(argv[0])
    missing: list[str] = []
    for module_name, page in MODULES.items():
        path = root / page
        if not path.exists():
            missing.append(f"{module_name}: no page {page}")
            continue
        anchors = set(re.findall(r'id="([^"]+)"', path.read_text(encoding="utf-8")))
        for name in public_names(module_name):
            if name not in anchors:
                missing.append(f"{module_name}.{name}")
    if missing:
        print("The reference is missing:\n" + "\n".join(f"  {m}" for m in missing))
        return 1
    print(f"The reference covers every public name of {len(MODULES)} modules.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
