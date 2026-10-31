"""A unified diff of two texts, the one an approval shows before a write.

The algorithm is spelled out here rather than taken from `difflib` so the
TypeScript SDK, which ports it line for line, prints the same diff for the
same change: Myers' shortest edit script over lines, after the common
prefix and suffix are set aside, with each run of changes printed as its
removals and then its additions. A change too large for the edit-distance
cap is printed as one block of removals and additions, which is still a
correct diff, only not the shortest.
"""

from __future__ import annotations

from ._contract import DIFF_CONTEXT_LINES, DIFF_MAX_EDIT_DISTANCE

NO_NEWLINE = "\\ No newline at end of file"

# An opcode: tag ("equal" or "change"), then a[i1:i2] and b[j1:j2].
_Opcode = tuple[str, int, int, int, int]


def split_lines(text: str) -> list[str]:
    """The lines of `text`, each with its `\\n`; the last keeps none when
    the text does not end in one. Only LF splits a line: a CR stays part of
    its line, so a CRLF file diffs as itself."""
    if text == "":
        return []
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1] != "":
        lines.append(parts[-1])
    return lines


def _myers(a: list[str], b: list[str], max_d: int) -> list[str] | None:
    """The shortest edit script from `a` to `b` as "e" (keep), "d" (remove
    from a) and "i" (insert from b), or None when it needs more than
    `max_d` edits."""
    n, m = len(a), len(b)
    limit = min(n + m, max_d)
    offset = limit + 1
    v = [0] * (2 * limit + 3)
    trace: list[list[int]] = []
    for d in range(limit + 1):
        trace.append(v[offset - d - 1 : offset + d + 2])
        for k in range(-d, d + 1, 2):
            if k == -d or (k != d and v[offset + k - 1] < v[offset + k + 1]):
                x = v[offset + k + 1]
            else:
                x = v[offset + k - 1] + 1
            y = x - k
            while x < n and y < m and a[x] == b[y]:
                x += 1
                y += 1
            v[offset + k] = x
            if x >= n and y >= m:
                return _backtrack(trace, n, m)
    return None


def _backtrack(trace: list[list[int]], n: int, m: int) -> list[str]:
    x, y = n, m
    ops: list[str] = []
    for d in range(len(trace) - 1, -1, -1):
        vd = trace[d]
        base = d + 1
        k = x - y
        down = k == -d or (k != d and vd[base + k - 1] < vd[base + k + 1])
        prev_k = k + 1 if down else k - 1
        prev_x = vd[base + prev_k]
        prev_y = prev_x - prev_k
        while x > prev_x and y > prev_y:
            ops.append("e")
            x -= 1
            y -= 1
        if d > 0:
            if x == prev_x:
                ops.append("i")
                y -= 1
            else:
                ops.append("d")
                x -= 1
    ops.reverse()
    return ops


def _opcodes(a: list[str], b: list[str], max_d: int) -> list[_Opcode]:
    n, m = len(a), len(b)
    prefix = 0
    while prefix < n and prefix < m and a[prefix] == b[prefix]:
        prefix += 1
    suffix = 0
    while suffix < n - prefix and suffix < m - prefix and a[n - 1 - suffix] == b[m - 1 - suffix]:
        suffix += 1
    mid_a, mid_b = a[prefix : n - suffix], b[prefix : m - suffix]
    ops = _myers(mid_a, mid_b, max_d)
    if ops is None:
        ops = ["d"] * len(mid_a) + ["i"] * len(mid_b)
    ops = ["e"] * prefix + ops + ["e"] * suffix

    codes: list[_Opcode] = []
    i = j = 0
    at = 0
    while at < len(ops):
        i1, j1 = i, j
        if ops[at] == "e":
            while at < len(ops) and ops[at] == "e":
                i += 1
                j += 1
                at += 1
            codes.append(("equal", i1, i, j1, j))
        else:
            while at < len(ops) and ops[at] != "e":
                if ops[at] == "d":
                    i += 1
                else:
                    j += 1
                at += 1
            codes.append(("change", i1, i, j1, j))
    return codes


def _groups(codes: list[_Opcode], context: int) -> list[list[_Opcode]]:
    """The opcodes cut into hunks with `context` equal lines around each
    change, as `difflib.SequenceMatcher.get_grouped_opcodes` cuts them."""
    if not codes:
        return []
    codes = list(codes)
    tag, i1, i2, j1, j2 = codes[0]
    if tag == "equal":
        codes[0] = (tag, max(i1, i2 - context), i2, max(j1, j2 - context), j2)
    tag, i1, i2, j1, j2 = codes[-1]
    if tag == "equal":
        codes[-1] = (tag, i1, min(i2, i1 + context), j1, min(j2, j1 + context))
    groups: list[list[_Opcode]] = []
    group: list[_Opcode] = []
    for tag, i1, i2, j1, j2 in codes:
        if tag == "equal" and i2 - i1 > 2 * context:
            group.append((tag, i1, min(i2, i1 + context), j1, min(j2, j1 + context)))
            groups.append(group)
            group = []
            i1, j1 = max(i1, i2 - context), max(j1, j2 - context)
        group.append((tag, i1, i2, j1, j2))
    if group and not (len(group) == 1 and group[0][0] == "equal"):
        groups.append(group)
    return [g for g in groups if any(code[0] != "equal" for code in g)]


def _range(start: int, stop: int) -> str:
    beginning = start + 1
    length = stop - start
    if length == 1:
        return str(beginning)
    if length == 0:
        beginning -= 1
    return f"{beginning},{length}"


def _emit(out: list[str], prefix: str, line: str) -> None:
    if line.endswith("\n"):
        out.append(prefix + line)
    else:
        out.append(prefix + line + "\n")
        out.append(NO_NEWLINE + "\n")


def unified_diff(old: str, new: str, path: str = "file", *, context: int | None = None) -> str:
    """The unified diff from `old` to `new`, headed `--- a/<path>` and
    `+++ b/<path>`, with `context` unchanged lines around each change (the
    contract's 3 by default). Empty when the texts are equal. A last line
    without a newline is marked the way `diff -u` marks it."""
    if old == new:
        return ""
    n_context = DIFF_CONTEXT_LINES if context is None else context
    a, b = split_lines(old), split_lines(new)
    out = [f"--- a/{path}\n", f"+++ b/{path}\n"]
    for group in _groups(_opcodes(a, b, DIFF_MAX_EDIT_DISTANCE), n_context):
        first, last = group[0], group[-1]
        out.append(f"@@ -{_range(first[1], last[2])} +{_range(first[3], last[4])} @@\n")
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                for line in a[i1:i2]:
                    _emit(out, " ", line)
                continue
            for line in a[i1:i2]:
                _emit(out, "-", line)
            for line in b[j1:j2]:
                _emit(out, "+", line)
    return "".join(out)
