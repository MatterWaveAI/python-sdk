"""The read tools over a real workspace: the walker glob and grep share,
and read_file, list_dir, glob and grep themselves, each returning the
text the contract's `outputs` spell or raising `PathRefused` or
`ToolError` (return `str(error)` as the result, with the error flag).

The walk is depth first, a directory's files before its subdirectories,
each in code-point order; it leaves out what the deny list refuses and
what the .gitignore and .matterwaveignore files from the root down ignore
(`SearchIgnore`), follows a link to a file only when it leads inside the
root to a path that is not denied, and never follows a link to a
directory. The `path` a search is given is searched even when it is
ignored itself.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Iterator, Mapping
from pathlib import Path

from .confine import DenyList, confine, resolve_links
from .ignore import SearchIgnore
from .output import (
    ToolError,
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


def _within(child: str, parent: str) -> bool:
    c = os.path.normcase(os.path.normpath(child))
    p = os.path.normcase(os.path.normpath(parent))
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


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
    that leads out of the root, dangles or lands on a denied path, or
    something that is neither a file nor a directory."""
    if _is_link(entry):
        target = resolve_links(entry.path)
        if not _within(str(target), str(real_root)) or not target.exists():
            return None
        rel = os.path.relpath(target, real_root).replace(os.sep, "/")
        is_dir = target.is_dir()
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


def walk(
    root: str | os.PathLike[str],
    path: str = ".",
    *,
    deny: DenyList | None = None,
    ignore: SearchIgnore | None = None,
) -> Iterator[str]:
    """The files a search under `path` sees, relative to the root with `/`
    separators, in walk order. `path` itself is confined (`PathRefused`)
    and must exist (`ToolError`); a file is its own one result."""
    real_root, deny_list, rules = _setup(root, deny, ignore)
    target = confine(root, path, deny=deny_list)
    if not target.absolute.exists():
        raise ToolError("not_found", path=target.relative)
    yield from _walk(real_root, target.relative, target.absolute.is_file(), rules)


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
    """read_file: the numbered lines of `path`, as `format_read_file`."""
    for name, value in (("start_line", start_line), ("end_line", end_line)):
        if value is not None and value < 1:
            raise ToolError("bad_line", name=name, value=value)
    if start_line is not None and end_line is not None and end_line < start_line:
        raise ToolError("end_before_start", start_line=start_line, end_line=end_line)
    target = confine(root, path, deny=deny)
    if target.absolute.is_dir():
        raise ToolError("is_directory", path=target.relative)
    if not target.absolute.is_file():
        raise ToolError("not_found", path=target.relative)
    size = target.absolute.stat().st_size
    cap = limit("read_max_file_bytes", limits)
    if size > cap:
        raise ToolError("too_large", path=target.relative, size=size, limit=cap)
    data = target.absolute.read_bytes()
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
    if target.absolute.is_file():
        raise ToolError("not_directory", path=target.relative)
    if not target.absolute.is_dir():
        raise ToolError("not_found", path=target.relative)
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
    real_root, deny_list, rules = _setup(root, deny, ignore)
    target = confine(root, path, deny=deny_list)
    if target.absolute.is_file():
        raise ToolError("not_directory", path=target.relative)
    if not target.absolute.is_dir():
        raise ToolError("not_found", path=target.relative)
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
) -> str:
    """grep: the lines matching `pattern` in the files under `path` (only
    those `glob` matches, when given), as `format_grep`."""
    if pattern == "":
        raise ToolError("empty_pattern")
    try:
        regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as exc:
        raise ToolError("invalid_regex", pattern=pattern, error=exc) from exc
    real_root, deny_list, rules = _setup(root, deny, ignore)
    target = confine(root, path, deny=deny_list)
    is_file = target.absolute.is_file()
    if not is_file and not target.absolute.is_dir():
        raise ToolError("not_found", path=target.relative)
    if is_file:
        base = target.relative.rsplit("/", 1)[0] if "/" in target.relative else ""
    else:
        base = "" if target.relative == "." else target.relative
    cap = limit("search_max_results", limits)
    max_file = limit("grep_max_file_bytes", limits)
    found: list[tuple[str, int, str]] = []
    for rel in _walk(real_root, target.relative, is_file, rules):
        if glob and not glob_matches(glob, _under(rel, base)):
            continue
        file = real_root / rel
        try:
            if file.stat().st_size > max_file:
                continue
            data = file.read_bytes()
        except OSError:
            continue
        if is_binary(data, limits):
            continue
        for number, line in enumerate(text_lines(decode_text(data)), start=1):
            if regex.search(line):
                if len(found) == cap:
                    return format_grep(pattern, found, truncated=True, limits=limits)
                found.append((rel, number, line))
    return format_grep(pattern, found, limits=limits)
