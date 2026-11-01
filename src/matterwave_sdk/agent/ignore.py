"""What glob, grep and list_dir leave out: the deny list, and the
.gitignore and .matterwaveignore files from the workspace root down.

`IgnoreFile` is one file's rules, for the directory holding it, in the
.gitignore syntax: comments, `!` to re-include, a trailing `/` for
directories only, a leading or middle `/` to anchor at that directory,
`**`, and a backslash to escape; a leading byte-order mark is dropped and
a line whose pattern is invalid (a `[...]` range that runs backwards) is
skipped, as git does. `SearchIgnore` stacks every file that
applies to a path, root first, with the deny list on top: the last rule
that matches decides, a path inside an ignored directory stays ignored,
and the deny list cannot be re-included. Matching ignores case, so every
client on every platform skips the same files.

Both are pure: `SearchIgnore` reads each directory's files through the
callable it is given, the first time a path under it is checked, so the
same rules serve a filesystem (`SearchIgnore.from_root`) or a client that
lists files another way.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ._contract import SEARCH_IGNORE_FILES
from .confine import DenyList, GlobError, glob_to_regex

_BOM = "\ufeff"


@dataclass(frozen=True)
class _Rule:
    regex: re.Pattern[str]
    negate: bool
    dir_only: bool
    anchored: bool


def _pattern_regex(pattern: str) -> str:
    """The regex for one .gitignore pattern: `glob_to_regex` on each run of
    plain text, a backslash making the character after it literal."""
    out: list[str] = []
    chunk: list[str] = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "\\" and i + 1 < len(pattern):
            out.append(glob_to_regex("".join(chunk)))
            chunk = []
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        chunk.append(c)
        i += 1
    out.append(glob_to_regex("".join(chunk)))
    return "".join(out)


def _parse(line: str) -> _Rule | None:
    text = line[:-1] if line.endswith("\r") else line
    if not text or text.startswith("#"):
        return None
    negate = text.startswith("!")
    if negate:
        text = text[1:]
    while text.endswith(" ") and not text.endswith("\\ "):
        text = text[:-1]
    dir_only = text.endswith("/")
    text = text.rstrip("/")
    anchored = "/" in text
    text = text.removeprefix("/")
    if not text:
        return None
    try:
        regex = re.compile(_pattern_regex(text), re.IGNORECASE)
    except (GlobError, re.error):
        return None
    return _Rule(regex, negate, dir_only, anchored)


class IgnoreFile:
    """The rules of one .gitignore-syntax file, for the directory holding it
    (`base`, relative to the workspace root, `""` for the root). A leading
    byte-order mark is dropped, and an invalid line is skipped."""

    def __init__(self, text: str, base: str = "") -> None:
        self.base = base.strip("/")
        self.patterns: tuple[str, ...] = tuple(text.removeprefix(_BOM).split("\n"))
        self._rules = [r for r in (_parse(line) for line in self.patterns) if r is not None]

    def match(self, relative: str, *, is_dir: bool = False) -> bool | None:
        """What this file says of `relative` (to the root, `/`-separated):
        True when its last matching rule ignores it, False when that rule
        re-includes it (`!`), None when no rule matches or the path is not
        under `base`. Only the path itself is matched; `SearchIgnore`
        checks the directories above it."""
        if self.base:
            if not relative.startswith(self.base + "/"):
                return None
            sub = relative[len(self.base) + 1 :]
        else:
            sub = relative
        name = sub.rsplit("/", 1)[-1]
        verdict: bool | None = None
        for rule in self._rules:
            if rule.dir_only and not is_dir:
                continue
            if rule.regex.fullmatch(sub if rule.anchored else name):
                verdict = not rule.negate
        return verdict


class SearchIgnore:
    """The deny list plus every ignore file that applies to a path. `read`
    takes a file's path relative to the root (`src/.gitignore`) and returns
    its text, or None when there is no such file; it is called once per
    file, when a path under its directory is first checked."""

    def __init__(self, read: Callable[[str], str | None], *, deny: DenyList | None = None) -> None:
        self.deny = deny if deny is not None else DenyList()
        self._read = read
        self._files: dict[str, tuple[IgnoreFile, ...]] = {}

    @classmethod
    def from_root(
        cls, root: str | os.PathLike[str], *, deny: DenyList | None = None
    ) -> SearchIgnore:
        """The rules of the workspace at `root`, its ignore files read from
        disk; `deny` defaults to `DenyList.from_root(root)`."""
        base = Path(root)

        def read(relative: str) -> str | None:
            target = base / relative
            try:
                if target.is_file():
                    return target.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return None
            return None

        return cls(read, deny=deny if deny is not None else DenyList.from_root(base))

    def files_for(self, directory: str) -> tuple[IgnoreFile, ...]:
        """The ignore files in `directory` (relative, `""` for the root),
        the .gitignore before the .matterwaveignore."""
        key = directory.strip("/")
        found = self._files.get(key)
        if found is None:
            loaded: list[IgnoreFile] = []
            for name in SEARCH_IGNORE_FILES:
                text = self._read(f"{key}/{name}" if key else name)
                if text is not None:
                    loaded.append(IgnoreFile(text, key))
            found = tuple(loaded)
            self._files[key] = found
        return found

    def _ignored_here(self, relative: str, is_dir: bool) -> bool:
        parts = relative.split("/")
        verdict: bool | None = None
        for depth in range(len(parts)):
            for file in self.files_for("/".join(parts[:depth])):
                said = file.match(relative, is_dir=is_dir)
                if said is not None:
                    verdict = said
        return verdict is True

    def ignored(self, relative: str, *, is_dir: bool = False, ancestors: bool = True) -> bool:
        """Whether a search leaves `relative` (to the root, `/`-separated)
        out: denied, ignored, or (with `ancestors`, the default) inside a
        directory that is. A walker that checks each directory before going
        into it passes `ancestors=False`."""
        relative = "/".join(s for s in relative.replace("\\", "/").split("/") if s and s != ".")
        if not relative:
            return False
        if self.deny.matches(relative, is_dir=is_dir):
            return True
        if ancestors:
            parts = relative.split("/")
            for depth in range(1, len(parts)):
                if self._ignored_here("/".join(parts[:depth]), True):
                    return True
        return self._ignored_here(relative, is_dir)


def load_search_ignore(root: str | os.PathLike[str]) -> SearchIgnore:
    """`SearchIgnore.from_root`, under the TypeScript SDK's name."""
    return SearchIgnore.from_root(root)
