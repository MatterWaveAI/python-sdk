"""What the read tools and run_shell return to the model, as the contract's
`outputs` spell it, with no filesystem in sight: the client reads the file
or runs the command, and these turn the result into the tool's text, so
every client numbers lines, marks a cut and words an error the same way.

`ToolError` carries the contract's sentence for a tool that cannot answer
(a missing file, a bad line range, an invalid regex); return `str(error)`
as the tool's result with the error flag set.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from functools import lru_cache

from ._contract import LIMITS, OUTPUTS, TOOL_ERRORS, ToolFailure, fill
from .confine import GlobError, glob_to_regex

_GREP = OUTPUTS["grep"]
_TRAILING_SPACE = " \t\r\f\v"


class ToolError(ValueError):
    """The tool cannot answer, and nothing was read or run. `reason` is the
    contract's code; `str(error)` is its sentence for the model with
    `values` filled in."""

    def __init__(self, reason: ToolFailure, **values: object) -> None:
        self.reason: ToolFailure = reason
        self.values = values
        super().__init__(fill(TOOL_ERRORS[reason], {k: str(v) for k, v in values.items()}))


def limit(name: str, limits: Mapping[str, int] | None = None) -> int:
    """`LIMITS[name]`, or the override `limits` gives for it."""
    if limits is not None and name in limits:
        return int(limits[name])
    return LIMITS[name]


def _seconds(value: float) -> str:
    """A number of seconds as the contract's messages print it: `5` for a
    whole number, `0.5` otherwise."""
    return str(int(value)) if float(value).is_integer() else str(value)


def check_lines(start_line: int | None = None, end_line: int | None = None) -> None:
    """The argument errors read_file checks before it looks at the file:
    `bad_line` for a line number under 1, then `end_before_start`."""
    for name, value in (("start_line", start_line), ("end_line", end_line)):
        if value is not None and value < 1:
            raise ToolError("bad_line", name=name, value=value)
    if start_line is not None and end_line is not None and end_line < start_line:
        raise ToolError("end_before_start", start_line=start_line, end_line=end_line)


def _utf8(text: str) -> bytes:
    return text.encode("utf-8", "surrogatepass")


def utf8_head(text: str, max_bytes: int) -> str:
    """The longest start of `text` whose UTF-8 fits in `max_bytes`."""
    data = _utf8(text)
    if len(data) <= max_bytes:
        return text
    cut = max(max_bytes, 0)
    while cut > 0 and (data[cut] & 0xC0) == 0x80:
        cut -= 1
    return data[:cut].decode("utf-8", "surrogatepass")


def utf8_tail(text: str, max_bytes: int) -> str:
    """The longest end of `text` whose UTF-8 fits in `max_bytes`."""
    data = _utf8(text)
    if len(data) <= max_bytes:
        return text
    start = len(data) - max(max_bytes, 0)
    while start < len(data) and (data[start] & 0xC0) == 0x80:
        start += 1
    return data[start:].decode("utf-8", "surrogatepass")


def is_binary(data: bytes, limits: Mapping[str, int] | None = None) -> bool:
    """Whether a file whose bytes start with `data` is binary: a NUL byte in
    its first `binary_sniff_bytes`."""
    return b"\0" in data[: limit("binary_sniff_bytes", limits)]


def decode_text(data: bytes) -> str:
    """A text file's bytes as text: UTF-8, an invalid byte becoming U+FFFD,
    a byte-order mark kept."""
    return data.decode("utf-8", errors="replace")


def text_lines(text: str) -> list[str]:
    """The lines of `text` as the read tools count them: split at LF, each
    line's trailing CR dropped, and a final LF starting no further line."""
    if text == "":
        return []
    parts = text.split("\n")
    if parts[-1] == "":
        parts.pop()
    return [p[:-1] if p.endswith("\r") else p for p in parts]


def format_read_file(
    text: str,
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
    *,
    limits: Mapping[str, int] | None = None,
) -> str:
    """read_file's result for a file whose text is `text`: the lines from
    `start_line` to `end_line`, numbered, cut at the read limits with the
    contract's marker. `ToolError` for a bad range."""
    spec = OUTPUTS["read_file"]
    check_lines(start_line, end_line)
    lines = text_lines(text)
    total = len(lines)
    if total == 0:
        return fill(spec["empty"], {"path": path})
    first = start_line or 1
    if first > total:
        raise ToolError("past_end", start_line=first, path=path, total=total)
    last_wanted = min(end_line or total, total)
    max_lines = limit("read_max_lines", limits)
    max_bytes = limit("read_max_bytes", limits)
    out: list[str] = []
    size = 0
    for n in range(first, last_wanted + 1):
        line = fill(spec["line"], {"n": str(n), "text": lines[n - 1]})
        cost = len(_utf8(line)) + 1
        if not out and len(_utf8(line)) > max_bytes:
            marker = fill(
                spec["line_cut"],
                {"line": str(n), "max_bytes": str(max_bytes), "next": str(n + 1)},
            )
            return utf8_head(line, max_bytes) + "\n" + marker
        if out and (len(out) >= max_lines or size + cost > max_bytes):
            break
        out.append(line)
        size += cost
    last = first + len(out) - 1
    body = "\n".join(out)
    if last < last_wanted:
        values = {"first": first, "last": last, "total": total, "next": last + 1}
        body += "\n" + fill(spec["cut"], {k: str(v) for k, v in values.items()})
    return body


def format_list_dir(
    path: str, entries: Iterable[tuple[str, bool]], *, limits: Mapping[str, int] | None = None
) -> str:
    """list_dir's result for the `(name, is_dir)` entries left once denied
    and ignored ones are out: directories first, then files, each in
    code-point order, cut at `list_max_entries`."""
    spec = OUTPUTS["list_dir"]
    pairs = list(entries)
    lines = [f"{name}/" for name in sorted(n for n, d in pairs if d)]
    lines += sorted(n for n, d in pairs if not d)
    if not lines:
        return fill(spec["empty"], {"path": path})
    cap = limit("list_max_entries", limits)
    if len(lines) > cap:
        more = len(lines) - cap
        lines = [*lines[:cap], fill(spec["cut"], {"more": str(more)})]
    return "\n".join(lines)


def format_glob(
    pattern: str,
    paths: Iterable[str],
    *,
    truncated: bool = False,
    limits: Mapping[str, int] | None = None,
) -> str:
    """glob's result: one path per line, or the no-match sentence;
    `truncated` adds the marker for a search that stopped at the limit."""
    spec = OUTPUTS["glob"]
    found = list(paths)
    if not found:
        return fill(spec["none"], {"pattern": pattern})
    if truncated:
        found.append(fill(spec["cut"], {"limit": str(limit("search_max_results", limits))}))
    return "\n".join(found)


def grep_line(text: str) -> str:
    """A matching line as grep shows it: trailing whitespace gone, cut to
    `line_max_chars` code points with an ellipsis."""
    text = text.rstrip(_TRAILING_SPACE)
    cap = int(_GREP["line_max_chars"])
    if len(text) > cap:
        return text[:cap] + str(_GREP["ellipsis"])
    return text


def format_grep(
    pattern: str,
    matches: Iterable[tuple[str, int, str]],
    *,
    truncated: bool = False,
    limits: Mapping[str, int] | None = None,
) -> str:
    """grep's result for `(path, line_number, line_text)` matches: one line
    each, or the no-match sentence; `truncated` adds the marker."""
    lines = [
        fill(_GREP["match"], {"path": p, "line": str(n), "text": grep_line(t)})
        for p, n, t in matches
    ]
    if not lines:
        return fill(_GREP["none"], {"pattern": pattern})
    if truncated:
        lines.append(fill(_GREP["cut"], {"limit": str(limit("search_max_results", limits))}))
    return "\n".join(lines)


def format_shell(
    output: str,
    *,
    exit_code: int | None = None,
    timed_out: bool = False,
    stopped: bool = False,
    timeout_s: float | None = None,
    limits: Mapping[str, int] | None = None,
    omitted: int = 0,
) -> str:
    """run_shell's result: the status line (a timeout, a stop by the user,
    the exit code, or an end by a signal when `exit_code` is None), then
    the output, its middle cut out past `shell_output_max_bytes`.

    `omitted` is for a client that kept only the start and the end of a
    long output: pass the two joined as `output` and the number of bytes
    dropped from between them as `omitted`. The text is cut as though it
    were the whole output, and the cut is always shown. Keep at least the
    first and the last half of `shell_output_max_bytes` (whole characters)
    and the result is the one the whole output would give."""
    spec = OUTPUTS["shell"]
    if omitted < 0:
        raise ValueError("omitted counts bytes and cannot be negative")
    if timed_out:
        seconds = limit("shell_timeout_s_default", limits) if timeout_s is None else timeout_s
        status = fill(spec["timeout"], {"timeout_s": _seconds(seconds)})
    elif stopped:
        status = spec["stopped"]
    elif exit_code is None:
        status = spec["signal"]
    else:
        status = fill(spec["exit"], {"code": str(exit_code)})
    if output == "" and omitted == 0:
        return f"{status}\n{spec['no_output']}"
    cap = limit("shell_output_max_bytes", limits)
    total = len(_utf8(output)) + omitted
    if total > cap or omitted > 0:
        head = utf8_head(output, cap // 2)
        # The tail comes from what follows the head, so text a client kept
        # short of the cap is never shown twice.
        tail = utf8_tail(output[len(head) :], cap - cap // 2)
        cut = total - len(_utf8(head)) - len(_utf8(tail))
        output = f"{head}\n{fill(spec['cut'], {'omitted': str(cut)})}\n{tail}"
    return f"{status}\n{output}"


@lru_cache(maxsize=256)
def _compile_glob(pattern: str) -> tuple[re.Pattern[str], bool]:
    text = pattern.removeprefix("./")
    anchored = text.startswith("/")
    text = text.lstrip("/")
    whole_path = anchored or "/" in text
    return re.compile(glob_to_regex(text), re.IGNORECASE), whole_path


def _glob_regex(pattern: str) -> tuple[re.Pattern[str], bool]:
    try:
        return _compile_glob(pattern)
    except (GlobError, re.error):
        raise ToolError("invalid_glob", glob=pattern) from None


def glob_matches(pattern: str, relative: str) -> bool:
    """Whether glob's `pattern` matches a file at `relative` (to the search's
    `path`, `/`-separated): the whole path when the pattern has a `/`,
    otherwise the file's name at any depth. Case is ignored. `ToolError`
    (`invalid_glob`) for a pattern whose `[...]` range runs backwards."""
    regex, whole_path = _glob_regex(pattern)
    subject = relative if whole_path else relative.rsplit("/", 1)[-1]
    return regex.fullmatch(subject) is not None
