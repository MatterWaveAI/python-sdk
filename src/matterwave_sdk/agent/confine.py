"""Path confinement: the check every tool runs before it touches a path.

`confine(root, path)` resolves `path` against the workspace root, follows
its links (symbolic links, and junctions on Windows) and returns where it
really leads, or raises `PathRefused` before anything is read or written:
for a `..` segment, an absolute path outside the root, a link that leads
out of it, a path on the deny list (by its own name or its target's), or,
for a write, a protected path. The refusal's message is the contract's
sentence for the model.

Confinement is not a sandbox. It keeps the file tools inside the root;
`run_shell` can reach anything the user can, which is why commands ask.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ._contract import DEFAULT_DENY, DENY_FILE, PROTECTED, REFUSALS, Refusal

_MAX_LINK_HOPS = 40
_DRIVE = re.compile(r"^[A-Za-z]:")
_SEPARATORS = re.compile(r"[\\/]")


class PathRefused(PermissionError):
    """The path is not one the agent may use. `reason` is the contract's
    code (`empty`, `parent`, `outside`, `symlink`, `denied`, `protected`);
    `str(error)` is the sentence to return to the model as the tool's
    result."""

    def __init__(self, reason: Refusal, path: str) -> None:
        self.reason: Refusal = reason
        self.path = path
        super().__init__(REFUSALS[reason].replace("{path}", path))


@dataclass(frozen=True)
class ConfinedPath:
    """Where a tool's path really leads: `absolute`, with every link
    followed, and `relative`, the same place relative to the resolved root
    with `/` separators (`.` for the root itself)."""

    absolute: Path
    relative: str


def glob_to_regex(glob: str) -> str:
    """The regular expression (to match whole) for one glob: `*` and `?`
    stay within a segment, `**/` spans any number of segments, `[...]` is
    a class (`[!...]` negated), everything else is literal."""
    out: list[str] = []
    i = 0
    while i < len(glob):
        c = glob[i]
        if c == "*":
            if glob.startswith("**", i):
                if glob.startswith("/", i + 2):
                    out.append("(?:.*/)?")
                    i += 3
                else:
                    out.append(".*")
                    i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            end = glob.find("]", i + 2)
            if end == -1:
                out.append("\\[")
            else:
                body = glob[i + 1 : end]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body.replace("\\", "\\\\") + "]")
                i = end + 1
                continue
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


@dataclass(frozen=True)
class _Pattern:
    regex: re.Pattern[str]
    dir_only: bool
    anchored: bool


def _compile(pattern: str) -> _Pattern | None:
    text = pattern.strip()
    if not text or text.startswith(("#", "!")):
        return None
    dir_only = text.endswith("/")
    text = text.rstrip("/")
    anchored = text.startswith("/") or "/" in text
    text = text.lstrip("/")
    if not text:
        return None
    return _Pattern(re.compile(glob_to_regex(text), re.IGNORECASE), dir_only, anchored)


class DenyList:
    """Patterns a path may not match: the contract's defaults (`.env*`,
    `*.pem`, `id_*`, `.git/`) plus the workspace's `.matterwaveignore`.
    The syntax is the contract's `pattern_syntax`, a subset of
    .gitignore's without negation."""

    def __init__(self, patterns: Iterable[str] = DEFAULT_DENY) -> None:
        self.patterns: tuple[str, ...] = tuple(patterns)
        self._compiled = [p for p in (_compile(x) for x in self.patterns) if p is not None]

    @classmethod
    def with_ignore_file(cls, text: str) -> DenyList:
        """The defaults plus every pattern line of a `.matterwaveignore`'s
        text. Lines end at LF or CRLF only, as the TypeScript SDK splits
        them."""
        return cls([*DEFAULT_DENY, *re.split(r"\r?\n", text)])

    @classmethod
    def from_root(cls, root: str | os.PathLike[str]) -> DenyList:
        """The defaults plus every pattern line of `<root>/.matterwaveignore`."""
        deny_file = Path(root) / DENY_FILE
        if deny_file.is_file():
            return cls.with_ignore_file(deny_file.read_text(encoding="utf-8", errors="replace"))
        return cls()

    def matches(self, relative: str, *, is_dir: bool = False) -> bool:
        """Whether `relative` (to the root, `/`-separated) is denied. A
        pattern that matches a directory on the way denies everything under
        it; `is_dir` says whether the last segment is a directory."""
        segments = [s for s in relative.replace("\\", "/").split("/") if s and s != "."]
        last = len(segments) - 1
        for pattern in self._compiled:
            for index in range(len(segments)):
                segment_is_dir = index < last or is_dir
                if pattern.dir_only and not segment_is_dir:
                    continue
                subject = "/".join(segments[: index + 1]) if pattern.anchored else segments[index]
                if pattern.regex.fullmatch(subject):
                    return True
        return False


_PROTECTED = DenyList(PROTECTED)


def load_deny_list(root: str | os.PathLike[str]) -> DenyList:
    """`DenyList.from_root`, under the TypeScript SDK's name."""
    return DenyList.from_root(root)


def is_absolute(path: str) -> bool:
    """Absolute on any platform: a leading `/` or `\\`, or a drive letter
    (`C:` and `C:\\`), so a Windows path is not taken for a relative one
    on POSIX, nor `/etc` for a relative one on Windows."""
    return path.startswith(("/", "\\")) or bool(_DRIVE.match(path))


def _strip_verbatim(path: str) -> str:
    for prefix in ("\\\\?\\", "\\??\\"):
        if path.startswith(prefix):
            return path[len(prefix) :]
    return path


def resolve_links(path: str | os.PathLike[str]) -> Path:
    """`path` with every link in it followed, whether or not its target
    exists: the deepest part that exists is resolved by the OS, and a
    dangling link is read and followed by hand, so a write through it is
    caught as well as a read."""
    current = os.path.abspath(path)
    for _ in range(_MAX_LINK_HOPS):
        head, rest = current, ""
        while not os.path.lexists(head):
            parent = os.path.dirname(head)
            if parent == head:
                break
            name = os.path.basename(head)
            rest = name if not rest else os.path.join(name, rest)
            head = parent
        if os.path.islink(head) and not os.path.exists(head):
            target = _strip_verbatim(os.readlink(head))
            base = target if os.path.isabs(target) else os.path.join(os.path.dirname(head), target)
            current = os.path.abspath(os.path.join(base, rest) if rest else base)
            continue
        real = os.path.realpath(head)
        return Path(os.path.join(real, rest) if rest else real)
    raise OSError(f"too many levels of links resolving {path}")


def _within(child: str, parent: str) -> bool:
    c = os.path.normcase(os.path.normpath(child))
    p = os.path.normcase(os.path.normpath(parent))
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


def _relative(child: str, parent: str) -> str:
    rel = os.path.relpath(child, parent)
    return "" if rel == "." else rel.replace(os.sep, "/")


def confine(
    root: str | os.PathLike[str],
    path: str,
    *,
    deny: DenyList | None = None,
    write: bool = False,
) -> ConfinedPath:
    """Where `path` leads inside `root`, or `PathRefused`. `deny` defaults
    to the root's `DenyList.from_root`; `write=True` also refuses the
    protected paths, which can be read but not changed."""
    if not path or path.strip() == "":
        raise PathRefused("empty", path)
    if "\0" in path:
        raise PathRefused("outside", path.replace("\0", ""))
    if ".." in _SEPARATORS.split(path):
        raise PathRefused("parent", path)

    given_root = os.path.abspath(root)
    real_root = str(resolve_links(given_root))
    if is_absolute(path):
        lexical = os.path.normpath(os.path.abspath(path))
        if _within(lexical, real_root):
            lexical_rel = _relative(lexical, real_root)
        elif _within(lexical, given_root):
            lexical_rel = _relative(lexical, given_root)
        else:
            raise PathRefused("outside", path)
    else:
        lexical = os.path.normpath(os.path.join(real_root, path))
        lexical_rel = _relative(lexical, real_root)

    real = resolve_links(lexical)
    if not _within(str(real), real_root):
        raise PathRefused("symlink", path)
    real_rel = _relative(str(real), real_root)

    deny_list = deny if deny is not None else DenyList.from_root(real_root)
    is_dir = real.is_dir()
    for rel in (lexical_rel, real_rel):
        if rel and deny_list.matches(rel, is_dir=is_dir):
            raise PathRefused("denied", path)
    if write:
        for rel in (lexical_rel, real_rel):
            if rel and _PROTECTED.matches(rel, is_dir=is_dir):
                raise PathRefused("protected", path)
    return ConfinedPath(absolute=real, relative=real_rel or ".")
