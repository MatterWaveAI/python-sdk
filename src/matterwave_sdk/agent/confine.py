"""Path confinement: the check every tool runs before it touches a path.

`confine(root, path)` resolves `path` against the workspace root, follows
its links (symbolic links, and junctions on Windows) and returns where it
really leads, or raises `PathRefused` before anything is read or written:
for a `..` segment, a name Windows would read as another file or a device
(a `:` stream, a trailing dot or space, `NUL` and the other reserved
names, a verbatim or UNC path; only the names below the root count), an
absolute path outside the root, a link that leads out of it or loops, a
path on the deny list (by its own name or its target's), or, for a write,
a protected path the user has not approved. The refusal's message is the
contract's sentence for the model, and nothing else escapes it: a path
the OS cannot make sense of, or a loop of links, is a `PathRefused` too,
never a bare ValueError or OSError.

Confinement is not a sandbox. It keeps the file tools inside the root;
`run_shell` can reach anything the user can, which is why commands ask.
"""

from __future__ import annotations

import errno
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ._contract import DEFAULT_DENY, DENY_FILE, PROTECTED, REFUSALS, Refusal

_MAX_LINK_HOPS = 40
_BOM = "\ufeff"
_DRIVE = re.compile(r"^[A-Za-z]:")
_DRIVE_ROOT = re.compile(r"^[A-Za-z]:(?=[\\/]|$)")
_SEPARATORS = re.compile(r"[\\/]")
_DEVICE = re.compile(
    r"con|prn|aux|nul|com[1-9\u00b9\u00b2\u00b3]|lpt[1-9\u00b9\u00b2\u00b3]|conin\$|conout\$",
    re.IGNORECASE,
)


class PathRefused(PermissionError):
    """The path is not one the agent may use. `reason` is the contract's
    code (`empty`, `parent`, `outside`, `symlink`, `loop`, `denied`,
    `protected`, and the Windows aliases `stream`, `trailing`, `device`,
    `unc`);
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


class GlobError(ValueError):
    """A glob the contract calls invalid: a `[...]` class with a range that
    runs backwards (`[z-a]`). `glob` is the pattern. glob and grep turn it
    into the `invalid_glob` ToolError; an ignore or deny file skips the
    line, as git does."""

    def __init__(self, glob: str) -> None:
        self.glob = glob
        super().__init__(f"{glob!r} has a [...] range that runs backwards")


def _glob_class(glob: str, start: int) -> tuple[str, int] | None:
    """The regex for the `[...]` class at `glob[start]` and the index after
    it, or None when no `]` closes it (the `[` is then literal). A `!` or
    `^` first negates it, a `]` right after that (or right after the `[`)
    is literal, `a-z` is a range and every other character is itself, each
    escaped, so every regex dialect reads the class alike."""
    i = start + 1
    negate = i < len(glob) and glob[i] in "!^"
    if negate:
        i += 1
    body_start = i
    if i < len(glob) and glob[i] == "]":
        i += 1
    end = glob.find("]", i)
    if end == -1:
        return None
    body = glob[body_start:end]
    out: list[str] = []
    k = 0
    while k < len(body):
        if k + 2 < len(body) and body[k + 1] == "-":
            low, high = body[k], body[k + 2]
            if ord(low) > ord(high):
                raise GlobError(glob)
            out.append(f"{re.escape(low)}-{re.escape(high)}")
            k += 3
        else:
            out.append(re.escape(body[k]))
            k += 1
    return "[" + ("^" if negate else "") + "".join(out) + "]", end + 1


def glob_to_regex(glob: str) -> str:
    """The regular expression (to match whole) for one glob: `*` and `?`
    stay within a segment, `**/` spans any number of segments, `[...]` is
    a class (`[!...]` or `[^...]` negated, a `]` first in it literal),
    everything else is literal. `GlobError` when a class's range runs
    backwards (`[z-a]`)."""
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
            found = _glob_class(glob, i)
            if found is not None:
                out.append(found[0])
                i = found[1]
                continue
            out.append("\\[")
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
    try:
        return _Pattern(re.compile(glob_to_regex(text), re.IGNORECASE), dir_only, anchored)
    except (GlobError, re.error):
        return None


class DenyList:
    """Patterns a path may not match: the contract's defaults (`.env*`,
    `*.pem`, `id_*`, `.git/`) plus the workspace's `.matterwaveignore`.
    The syntax is the contract's `pattern_syntax`, a subset of
    .gitignore's without negation; a line whose pattern is invalid (a
    `[...]` range that runs backwards) is skipped, as git skips it."""

    def __init__(self, patterns: Iterable[str] = DEFAULT_DENY) -> None:
        self.patterns: tuple[str, ...] = tuple(patterns)
        self._compiled = [p for p in (_compile(x) for x in self.patterns) if p is not None]

    @classmethod
    def with_ignore_file(cls, text: str) -> DenyList:
        """The defaults plus every pattern line of a `.matterwaveignore`'s
        text, a leading byte-order mark dropped. Lines end at LF or CRLF
        only, as the TypeScript SDK splits them."""
        return cls([*DEFAULT_DENY, *re.split(r"\r?\n", text.removeprefix(_BOM))])

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


PROTECTED_LIST = DenyList(PROTECTED)
"""The protected paths (`PROTECTED`) as a matcher: the deny file, the
agent's settings folder (`.matterwave/`), the context files (MATTERWAVE.md,
AGENTS.md, MATTERWAVE.local.md), and what matters when the root is the home
folder or above it: the shell startup files (`.bashrc`, `.bash_profile`,
`.profile`, `.zshrc`, `.zprofile`), the credential folders (`.ssh/`,
`.aws/`) and `.config/matterwave` at any depth. They can be read; a write
to one asks the user first in every mode that allows writes, yolo included
(`allowed(..., path=...)` says `ask`), and `confine` refuses it unless
`approved=True`."""


def is_protected(relative: str, *, is_dir: bool = False) -> bool:
    """Whether `relative` (to the root, `/`-separated) is one of the
    protected paths (`PROTECTED_LIST`), so a write to it needs the user's
    approval in every mode."""
    return PROTECTED_LIST.matches(relative, is_dir=is_dir)


def load_deny_list(root: str | os.PathLike[str]) -> DenyList:
    """`DenyList.from_root`, under the TypeScript SDK's name."""
    return DenyList.from_root(root)


def is_absolute(path: str) -> bool:
    """Absolute on any platform: a leading `/` or `\\`, or a drive letter
    (`C:` and `C:\\`), so a Windows path is not taken for a relative one
    on POSIX, nor `/etc` for a relative one on Windows."""
    return path.startswith(("/", "\\")) or bool(_DRIVE.match(path))


def _names(path: str) -> list[str]:
    return [n for n in _SEPARATORS.split(path) if n not in ("", ".")]


def windows_alias(path: str, root: str | os.PathLike[str] | None = None) -> Refusal | None:
    """The refusal for a path Windows could read as something other than
    the file it seems to name, or None: `unc` for a `\\\\?\\`, `\\\\.\\`,
    `\\??\\` or network path, then, name by name, `stream` for a `:` (an
    alternate data stream, or a drive-relative `C:x`), `trailing` for a
    name that ends in a dot or a space, and `device` for CON, PRN, AUX,
    NUL, COM1-9, LPT1-9, CONIN$ or CONOUT$, in any case and with or without
    an extension. `confine` applies it on every platform, so the clients
    agree on what they refuse.

    With `root`, the names are those below the root: an absolute path
    whose leading names are the root's (compared name by name, case
    ignored, `/` and `\\` both separators) is checked from the name after
    them, so a root inside `/x/con/` still takes absolute paths into it.
    Any other path is checked whole. Pure: nothing is read from disk."""
    if len(path) >= 2 and path[0] in "\\/" and path[1] in "\\/":
        return "unc"
    if path[:4] in ("\\??\\", "/??/"):
        return "unc"
    names: list[str] | None = None
    if root is not None and is_absolute(path):
        root_names = [n.lower() for n in _names(os.fspath(root))]
        given = _names(path)
        if [n.lower() for n in given[: len(root_names)]] == root_names:
            names = given[len(root_names) :]
    if names is None:
        names = _names(_DRIVE_ROOT.sub("", path, count=1))
    for name in names:
        if ":" in name:
            return "stream"
        if name[-1] in ". ":
            return "trailing"
        if _DEVICE.fullmatch(name.split(".", 1)[0].rstrip(" ")):
            return "device"
    return None


def _strip_verbatim(path: str) -> str:
    for prefix in ("\\\\?\\", "\\??\\"):
        if path.startswith(prefix):
            return path[len(prefix) :]
    return path


def resolve_links(path: str | os.PathLike[str]) -> Path:
    """`path` with every link in it followed, whether or not its target
    exists: the deepest part that exists is resolved by the OS, and a
    dangling link is read and followed by hand, so a write through it is
    caught as well as a read. An OSError with `errno.ELOOP` when the links
    loop, or take more than 40 hops."""
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
    raise OSError(errno.ELOOP, "too many levels of links", os.fspath(path))


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
    approved: bool = False,
) -> ConfinedPath:
    """Where `path` leads inside `root`, or `PathRefused`. `deny` defaults
    to the root's `DenyList.from_root`; `write=True` also refuses the
    protected paths unless `approved=True` says the user approved this
    write, since a change to one always asks first."""
    if not path or path.strip() == "":
        raise PathRefused("empty", path)
    if "\0" in path:
        raise PathRefused("outside", path.replace("\0", ""))
    if ".." in _SEPARATORS.split(path):
        raise PathRefused("parent", path)
    try:
        given_root = os.path.abspath(root)
        real_root = str(resolve_links(given_root))
        # Only the names below the root are the agent's; the folders above
        # it are the user's, whatever they are called.
        alias = windows_alias(path, given_root)
        if alias is not None and real_root != given_root:
            alias = windows_alias(path, real_root)
        if alias is not None:
            raise PathRefused(alias, path)
        return _confine(given_root, real_root, path, deny, write and not approved)
    except PathRefused:
        raise
    except ValueError:
        # A path the OS maps somewhere relpath cannot follow (another
        # drive, a device) is outside the root as far as the tools go.
        raise PathRefused("outside", path) from None
    except OSError as exc:
        reason: Refusal = "loop" if exc.errno == errno.ELOOP else "outside"
        raise PathRefused(reason, path) from None


def _confine(
    given_root: str, real_root: str, path: str, deny: DenyList | None, write: bool
) -> ConfinedPath:
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
            if rel and PROTECTED_LIST.matches(rel, is_dir=is_dir):
                raise PathRefused("protected", path)
    return ConfinedPath(absolute=real, relative=real_rel or ".")
