"""The vision helper, mirrored from the web app's vision.ts. The tri-state
is the API's `supports_vision: true | false | null`, and every shade of
silence is "unknown" -- the refusing verdict is only "no"."""

from __future__ import annotations

from matterwave_sdk import AppModel, vision_of

ROWS = [
    {"id": "grt-vision", "label": "GRT Vision", "supports_vision": True},
    {"id": "grt-nano", "label": "GRT Nano", "supports_vision": False},
    {"id": "grt-mum", "label": "GRT Mum", "supports_vision": None},
    {"id": "grt-bare", "label": "GRT Bare"},
]


def test_yes_no_and_null_are_read_as_they_are_sent() -> None:
    assert vision_of(ROWS, "grt-vision") == "yes"
    assert vision_of(ROWS, "grt-nano") == "no"
    # Null is "the gateway did not say", not "no": allowed, and the
    # server's 422 is the authority.
    assert vision_of(ROWS, "grt-mum") == "unknown"


def test_a_row_without_the_field_and_a_model_absent_from_the_list_are_unknown() -> None:
    assert vision_of(ROWS, "grt-bare") == "unknown"
    assert vision_of(ROWS, "grt-someday") == "unknown"


def test_a_null_model_and_a_null_list_are_unknown() -> None:
    # The picker's "Default" resolves server-side; the client cannot know
    # which model that is, so it does not guess.
    assert vision_of(ROWS, None) == "unknown"
    assert vision_of(None, "grt-vision") == "unknown"
    assert vision_of(None, None) == "unknown"


def test_app_model_rows_work_like_dicts() -> None:
    models = [
        AppModel(id="a", label="A", included=True, supports_vision=True),
        AppModel(id="b", label="B", included=True, supports_vision=False),
        AppModel(id="c", label="C", included=True, supports_vision=None),
    ]
    assert vision_of(models, "a") == "yes"
    assert vision_of(models, "b") == "no"
    assert vision_of(models, "c") == "unknown"
    assert vision_of(models, "d") == "unknown"
    assert vision_of(models, None) == "unknown"


def test_the_refusing_verdict_is_only_no() -> None:
    # The whole point of the tri-state: gate before the upload, refuse on
    # "no" alone, and let the server's 422 be the authority for the rest.
    refusals = {vision_of(ROWS, mid) == "no" for mid in ("grt-vision", "grt-nano", "grt-mum")}
    assert refusals == {False, True}
