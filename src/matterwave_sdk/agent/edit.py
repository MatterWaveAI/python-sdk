"""`edit`'s exact-replace semantics, on text, with no file in sight: the
client reads the file, calls `apply_edit`, shows the diff, and writes the
result only once it is approved."""

from __future__ import annotations

from ._contract import EDIT_CRLF_FALLBACK, EDIT_ERRORS, EditFailure


class EditError(ValueError):
    """The edit cannot be applied, and the text is unchanged. `reason` is
    the contract's code for why; `str(error)` is the contract's sentence
    for the model, with the path filled in."""

    def __init__(self, reason: EditFailure, path: str = "", count: int = 0) -> None:
        self.reason: EditFailure = reason
        self.path = path
        self.count = count
        message = EDIT_ERRORS[reason].replace("{path}", path).replace("{count}", str(count))
        super().__init__(message)


def _lf_to_crlf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


def occurrences(text: str, needle: str) -> int:
    """How many places `needle` starts in `text`, overlapping ones counted:
    "aa" occurs twice in "aaa", so an edit of it is not unique."""
    count = 0
    at = text.find(needle)
    while at != -1:
        count += 1
        at = text.find(needle, at + 1)
    return count


def apply_edit(text: str, old_string: str, new_string: str, *, path: str = "") -> str:
    """`text` with the one occurrence of `old_string` replaced by
    `new_string`. `EditError` when `old_string` is empty, equals
    `new_string`, is not in `text`, or is in it more than once. When it is
    not found as given and `text` uses CRLF line endings, the lone LFs of
    both strings become CRLF and the match is tried once more, so a model
    that writes LF still edits a Windows file without mixing endings."""
    if old_string == "":
        raise EditError("empty", path)
    if old_string == new_string:
        raise EditError("unchanged", path)
    count = occurrences(text, old_string)
    if count == 0 and EDIT_CRLF_FALLBACK and "\r\n" in text:
        crlf_old = _lf_to_crlf(old_string)
        if crlf_old != old_string:
            old_string, new_string = crlf_old, _lf_to_crlf(new_string)
            count = occurrences(text, old_string)
    if count == 0:
        raise EditError("not_found", path)
    if count > 1:
        raise EditError("not_unique", path, count)
    return text.replace(old_string, new_string, 1)
