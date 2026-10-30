"""The MCP example in examples/: its PEP 723 dependency block names only
real packages (a typo there fails for everyone who runs the script), its
module-level imports are the stdlib plus the SDK that block names, and its
guard asks for a key before it touches the network. The SDK itself is the
package under test, so the example's own code is what runs.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import sys
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "mcp_via_sdk.py"


def _normal(name: str) -> str:
    """PEP 503 normalization: `matterwave_sdk` and `matterwave-sdk` agree."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _dependency_block() -> list[str]:
    """The `dependencies = [...]` line of the script's PEP 723 header."""
    text = EXAMPLE.read_text(encoding="utf-8")
    block = re.search(r"# /// script\n(.*?)# ///", text, re.DOTALL)
    assert block is not None, "the example carries no PEP 723 script block"
    match = re.search(r"dependencies\s*=\s*(\[.*\])", block.group(1))
    assert match is not None, "the script block names no dependencies"
    return list(ast.literal_eval(match.group(1)))


def test_the_dependency_block_names_the_sdk_and_nothing_uninstallable() -> None:
    dependencies = _dependency_block()
    named = {
        _normal(d.split(";")[0].split(">")[0].split("<")[0].split("[")[0].strip())
        for d in dependencies
    }
    assert "matterwave-sdk" in named, dependencies
    # Everything the script imports at module level is either the stdlib or
    # named in the block, so `uv run` can actually install its world.
    tree = ast.parse(EXAMPLE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(_normal(alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(_normal(node.module))
    unknown = imported - named - {_normal(n) for n in sys.stdlib_module_names}
    assert unknown == set(), unknown


def test_the_example_imports_and_asks_for_a_key_before_touching_the_network(
    monkeypatch, capsys
) -> None:
    spec = importlib.util.spec_from_file_location("mcp_via_sdk_example", EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.delenv("MATTERWAVE_API_KEY", raising=False)
    exit_code = module.main()
    assert exit_code == 2
    assert "MATTERWAVE_API_KEY" in capsys.readouterr().err
