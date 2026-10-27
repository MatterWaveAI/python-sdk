"""The MCP client example in examples/: its PEP 723 dependency block names
only real packages (a typo there fails for everyone who runs the script),
it compiles, and its guard asks for a key before it touches the network.
The `mcp` package itself is not a dev dependency, so it is stood in for
while the module is imported -- the example's own code is what runs.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import re
import sys
import types
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "mcp_client.py"


def _dependency_block() -> list[str]:
    """The `dependencies = [...]` line of the script's PEP 723 header."""
    text = EXAMPLE.read_text(encoding="utf-8")
    block = re.search(r"# /// script\n(.*?)# ///", text, re.DOTALL)
    assert block is not None, "the example carries no PEP 723 script block"
    match = re.search(r"dependencies\s*=\s*(\[.*\])", block.group(1))
    assert match is not None, "the script block names no dependencies"
    return list(ast.literal_eval(match.group(1)))


def test_the_dependency_block_names_httpx_and_never_a_fork() -> None:
    dependencies = _dependency_block()
    assert any(d == "httpx" or d.startswith("httpx") for d in dependencies), dependencies
    assert not any(d.split(">")[0].split("<")[0].strip() == "httpx2" for d in dependencies)
    # Everything the script imports at module level is either the stdlib or
    # named in the block, so `uv run` can actually install its world.
    tree = ast.parse(EXAMPLE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    named = {d.split(">")[0].split("<")[0].split("[")[0].strip() for d in dependencies}
    unknown = imported - named - set(sys.stdlib_module_names)
    assert unknown == set(), unknown


def _import_with_stubbed_mcp() -> types.ModuleType:
    mcp = types.ModuleType("mcp")
    mcp.Client = object  # the example only binds the name
    streamable = types.ModuleType("mcp.client.streamable_http")
    streamable.streamable_http_client = object
    mcp.client = types.ModuleType("mcp.client")
    mcp.client.streamable_http = streamable
    for name, module in {
        "mcp": mcp,
        "mcp.client": mcp.client,
        "mcp.client.streamable_http": streamable,
    }.items():
        sys.modules.setdefault(name, module)
    spec = importlib.util.spec_from_file_location("mcp_client_example", EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_example_asks_for_a_key_before_touching_the_network(
    monkeypatch, capsys
) -> None:
    module = _import_with_stubbed_mcp()
    monkeypatch.delenv("MATTERWAVE_API_KEY", raising=False)
    exit_code = asyncio.run(module.main("pumpkin soup"))
    assert exit_code == 2
    assert "MATTERWAVE_API_KEY" in capsys.readouterr().err
