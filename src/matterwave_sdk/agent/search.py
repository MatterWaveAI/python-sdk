"""The read tools over a real workspace: the walker glob and grep share,
and read_file, list_dir, glob and grep themselves, each returning the
text the contract's `outputs` spell or raising `PathRefused` or
`ToolError` (return `str(error)` as the result, with the error flag).

The walk is depth first, a directory's files before its subdirectories,
each in code-point order; it leaves out what the deny list refuses and
what the .gitignore and .matterwaveignore files from the root down ignore
(`SearchIgnore`), follows a link to a file only when it leads inside the
root to a path that is not denied, and never follows a link to a
directory. Only regular files and directories are walked: a pipe, socket
or device (or a link to one) is left out, a tool given one as its `path`
answers `special_file`, and no tool ever waits on one. The `path` a search
is given is searched even when it is ignored itself.
"""

from __future__ import annotations

import os
import re
import stat
import threading
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Literal

from .confine import DenyList, confine, resolve_links
from .ignore import SearchIgnore
from .output import (
    ToolError,
    _glob_regex,
    _seconds,
    check_lines,
    decode_text,
    format_glob,
    format_grep,
    format_list_dir,
    format_read_file,
    glob_matches,
    is_binary,
    limit,
    text_lines,
)

_REPARSE_POINT = 0x400
# Non-blocking, so a pipe swapped in after its kind was checked is never
# waited on; binary, so Windows reads the bytes as they are.
_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_BINARY", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_NOCTTY", 0)
)
_CHUNK = 1 << 20

_Kind = Literal["file", "dir", "special"]


def _within(child: str, parent: str) -> bool:
    c = os.path.normcase(os.path.normpath(child))
    p = os.path.normcase(os.path.normpath(parent))
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


def _kind_at(path: str | os.PathLike[str]) -> _Kind | None:
    """What is at `path`, its links followed: a regular file, a directory,
    something else (a pipe, a socket, a device), or None for nothing."""
    try:
        mode = os.stat(path).st_mode
    except (OSError, ValueError):
        return None
    if stat.S_ISDIR(mode):
        return "dir"
    if stat.S_ISREG(mode):
        return "file"
    return "special"


def _read_capped(path: str | os.PathLike[str], cap: int) -> tuple[bytes, int] | None:
    """The bytes of the regular file at `path`, no more than `cap` + 1 of
    them, and its size (at least the bytes read, so a file that grew past
    `cap` still counts as over it); nothing is read when its size is already
    over `cap`. None when it is not a regular file."""
    fd = os.open(path, _OPEN_FLAGS)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return None
        if info.st_size > cap:
            return b"", info.st_size
        chunks: list[bytes] = []
        size = 0
        while size <= cap:
            chunk = os.read(fd, min(cap + 1 - size, _CHUNK, max(info.st_size - size + 1, 8192)))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        return b"".join(chunks), max(info.st_size, size)
    finally:
        os.close(fd)


def _is_link(entry: os.DirEntry[str]) -> bool:
    if entry.is_symlink():
        return True
    try:
        attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & _REPARSE_POINT)


def _kind(entry: os.DirEntry[str], real_root: Path, deny: DenyList) -> tuple[bool, bool] | None:
    """`(is_dir, is_link)` for an entry the tools may show, or None: a link
    that leads out of the root, dangles, loops or lands on a denied path,
    or anything that is neither a regular file nor a directory, a link's
    target included."""
    if _is_link(entry):
        try:
            target = resolve_links(entry.path)
        except OSError:
            return None
        if not _within(str(target), str(real_root)):
            return None
        kind = _kind_at(target)
        if kind not in ("file", "dir"):
            return None
        is_dir = kind == "dir"
        rel = os.path.relpath(target, real_root).replace(os.sep, "/")
        if rel != "." and deny.matches(rel, is_dir=is_dir):
            return None
        return is_dir, True
    try:
        mode = entry.stat(follow_symlinks=False).st_mode
    except OSError:
        return None
    if stat.S_ISDIR(mode):
        return True, False
    if stat.S_ISREG(mode):
        return False, False
    return None


def _entries(real_root: Path, directory: str, rules: SearchIgnore) -> list[tuple[str, bool, bool]]:
    """`(relative, is_dir, is_link)` for each entry of `directory` a search
    shows, in code-point order of the names."""
    absolute = real_root / directory if directory else real_root
    try:
        with os.scandir(absolute) as it:
            found = sorted(it, key=lambda e: e.name)
    except OSError:
        return []
    shown: list[tuple[str, bool, bool]] = []
    for entry in found:
        kind = _kind(entry, real_root, rules.deny)
        if kind is None:
            continue
        rel = f"{directory}/{entry.name}" if directory else entry.name
        if rules.ignored(rel, is_dir=kind[0], ancestors=False):
            continue
        shown.append((rel, kind[0], kind[1]))
    return shown


def _setup(
    root: str | os.PathLike[str], deny: DenyList | None, ignore: SearchIgnore | None
) -> tuple[Path, DenyList, SearchIgnore]:
    real_root = resolve_links(root)
    deny_list = deny if deny is not None else DenyList.from_root(real_root)
    rules = ignore if ignore is not None else SearchIgnore.from_root(real_root, deny=deny_list)
    return real_root, deny_list, rules


def _kind_of_target(absolute: Path, relative: str) -> Literal["file", "dir"]:
    """A tool's `path` as a file or a directory, or the ToolError for
    nothing there (`not_found`) or a pipe, socket or device
    (`special_file`)."""
    kind = _kind_at(absolute)
    if kind is None:
        raise ToolError("not_found", path=relative)
    if kind == "special":
        raise ToolError("special_file", path=relative)
    return kind


def walk(
    root: str | os.PathLike[str],
    path: str = ".",
    *,
    deny: DenyList | None = None,
    ignore: SearchIgnore | None = None,
) -> Iterator[str]:
    """The files a search under `path` sees, relative to the root with `/`
    separators, in walk order. `path` itself is confined (`PathRefused`)
    and must be a file or a directory (`ToolError`); a file is its own one
    result."""
    real_root, deny_list, rules = _setup(root, deny, ignore)
    target = confine(root, path, deny=deny_list)
    kind = _kind_of_target(target.absolute, target.relative)
    yield from _walk(real_root, target.relative, kind == "file", rules)


def _walk(real_root: Path, start: str, is_file: bool, rules: SearchIgnore) -> Iterator[str]:
    if is_file:
        yield start
        return
    stack = ["" if start == "." else start]
    while stack:
        directory = stack.pop()
        subdirs: list[str] = []
        for rel, is_dir, is_link in _entries(real_root, directory, rules):
            if not is_dir:
                yield rel
            elif not is_link:
                subdirs.append(rel)
        stack.extend(reversed(subdirs))


def run_read_file(
    root: str | os.PathLike[str],
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
    *,
    deny: DenyList | None = None,
    limits: Mapping[str, int] | None = None,
) -> str:
    """read_file: the numbered lines of `path`, as `format_read_file`. No
    more than `read_max_file_bytes` + 1 bytes are read, and a pipe or
    device is never opened to wait on."""
    check_lines(start_line, end_line)
    target = confine(root, path, deny=deny)
    if _kind_of_target(target.absolute, target.relative) == "dir":
        raise ToolError("is_directory", path=target.relative)
    cap = limit("read_max_file_bytes", limits)
    read = _read_capped(target.absolute, cap)
    if read is None:
        raise ToolError("special_file", path=target.relative)
    data, size = read
    if size > cap:
        raise ToolError("too_large", path=target.relative, size=size, limit=cap)
    if is_binary(data, limits):
        raise ToolError("binary", path=target.relative)
    return format_read_file(decode_text(data), target.relative, start_line, end_line, limits=limits)


def run_list_dir(
    root: str | os.PathLike[str],
    path: str = ".",
    *,
    deny: DenyList | None = None,
    ignore: SearchIgnore | None = None,
    limits: Mapping[str, int] | None = None,
) -> str:
    """list_dir: the entries of `path`, as `format_list_dir`."""
    real_root, deny_list, rules = _setup(root, deny, ignore)
    target = confine(root, path, deny=deny_list)
    if _kind_of_target(target.absolute, target.relative) == "file":
        raise ToolError("not_directory", path=target.relative)
    directory = "" if target.relative == "." else target.relative
    entries = [
        (rel.rsplit("/", 1)[-1], is_dir) for rel, is_dir, _ in _entries(real_root, directory, rules)
    ]
    return format_list_dir(target.relative, entries, limits=limits)


def _under(relative: str, base: str) -> str:
    return relative[len(base) + 1 :] if base else relative


def run_glob(
    root: str | os.PathLike[str],
    pattern: str,
    path: str = ".",
    *,
    deny: DenyList | None = None,
    ignore: SearchIgnore | None = None,
    limits: Mapping[str, int] | None = None,
) -> str:
    """glob: the files under `path` that `pattern` matches, as `format_glob`."""
    if pattern.strip() == "":
        raise ToolError("empty_pattern")
    _glob_regex(pattern)
    real_root, deny_list, rules = _setup(root, deny, ignore)
    target = confine(root, path, deny=deny_list)
    if _kind_of_target(target.absolute, target.relative) == "file":
        raise ToolError("not_directory", path=target.relative)
    base = "" if target.relative == "." else target.relative
    cap = limit("search_max_results", limits)
    found: list[str] = []
    truncated = False
    for rel in _walk(real_root, target.relative, False, rules):
        if glob_matches(pattern, _under(rel, base)):
            if len(found) == cap:
                truncated = True
                break
            found.append(rel)
    return format_glob(pattern, found, truncated=truncated, limits=limits)


class _Stopped(Exception):
    """The caller stopped waiting for the search; it ends where it is."""


def run_grep(
    root: str | os.PathLike[str],
    pattern: str,
    path: str = ".",
    *,
    glob: str | None = None,
    ignore_case: bool = False,
    deny: DenyList | None = None,
    ignore: SearchIgnore | None = None,
    limits: Mapping[str, int] | None = None,
    timeout_s: float | None = None,
) -> str:
    """grep: the lines matching `pattern` in the files under `path` (only
    those `glob` matches, when given), as `format_grep`.

    `timeout_s` bounds the search: it runs on a thread of its own, and when
    it has not finished in time the call raises `ToolError` (`timeout`) and
    abandons it; the thread ends at the next line it would match. Python's
    `re` holds the interpreter while it matches one line, so a pattern that
    takes exponential time on a single line keeps the call waiting until
    that one match ends; run the search in another process to bound that
    too."""
    if pattern == "":
        raise ToolError("empty_pattern")
    try:
        regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as exc:
        raise ToolError("invalid_regex", pattern=pattern, error=exc) from exc
    if glob:
        _glob_regex(glob)
    if timeout_s is not None and not timeout_s > 0:
        raise ValueError("timeout_s must be more than 0")
    real_root, deny_list, rules = _setup(root, deny, ignore)
    target = confine(root, path, deny=deny_list)
    is_file = _kind_of_target(target.absolute, target.relative) == "file"
    if is_file:
        base = target.relative.rsplit("/", 1)[0] if "/" in target.relative else ""
    else:
        base = "" if target.relative == "." else target.relative
    cap = limit("search_max_results", limits)
    max_file = limit("grep_max_file_bytes", limits)

    def search(stop: threading.Event | None) -> str:
        found: list[tuple[str, int, str]] = []
        for rel in _walk(real_root, target.relative, is_file, rules):
            if stop is not None and stop.is_set():
                raise _Stopped
            if glob and not glob_matches(glob, _under(rel, base)):
                continue
            try:
                read = _read_capped(real_root / rel, max_file)
            except OSError:
                continue
            if read is None or read[1] > max_file or is_binary(read[0], limits):
                continue
            for number, line in enumerate(text_lines(decode_text(read[0])), start=1):
                if stop is not None and stop.is_set():
                    raise _Stopped
                if regex.search(line):
                    if len(found) == cap:
                        return format_grep(pattern, found, truncated=True, limits=limits)
                    found.append((rel, number, line))
        return format_grep(pattern, found, limits=limits)

    if timeout_s is None:
        return search(None)
    stop = threading.Event()
    outcome: list[str] = []
    failure: list[BaseException] = []

    def run() -> None:
        try:
            outcome.append(search(stop))
        except _Stopped:
            pass
        except BaseException as exc:
            failure.append(exc)

    worker = threading.Thread(target=run, name="matterwave-grep", daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        stop.set()
        raise ToolError("timeout", pattern=pattern, timeout_s=_seconds(timeout_s))
    if failure:
        raise failure[0]
    return outcome[0]
