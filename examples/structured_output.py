# /// script
# requires-python = ">=3.10"
# dependencies = ["matterwave-sdk>=0.5"]
# ///
"""Ask for JSON and get it back validated: `chat.completions.parse`.

    MATTERWAVE_BASE_URL=https://<your-matterwave> MATTERWAVE_API_KEY=mw_... \\
        uv run examples/structured_output.py "pancakes"

`parse` asks the model for a JSON object and hands the answer to
`response_model`'s `model_validate`. A pydantic model is the usual choice;
this example validates with a dataclass of its own so it needs nothing but
the SDK, and anything with a `model_validate` classmethod works the same.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any

from matterwave_sdk import APIError, MatterWave, ParseError


@dataclass(frozen=True)
class Recipe:
    name: str
    minutes: int
    ingredients: list[str]

    @classmethod
    def model_validate(cls, data: Any) -> Recipe:
        if not isinstance(data, dict):
            raise ValueError("a recipe is a JSON object")
        name, minutes, ingredients = data.get("name"), data.get("minutes"), data.get("ingredients")
        if not isinstance(name, str) or not isinstance(minutes, int):
            raise ValueError("a recipe needs a name and whole minutes")
        if not isinstance(ingredients, list) or not all(isinstance(i, str) for i in ingredients):
            raise ValueError("ingredients is a list of strings")
        return cls(name=name, minutes=minutes, ingredients=ingredients)


def run(client: MatterWave, dish: str) -> int:
    prompt = (
        f"Give a recipe for {dish} as a JSON object with `name` (string), "
        "`minutes` (integer) and `ingredients` (list of strings)."
    )
    try:
        recipe = client.chat.completions.parse(
            [{"role": "user", "content": prompt}], response_model=Recipe
        )
    except ParseError as err:
        print(f"the answer was not JSON: {err}", file=sys.stderr)
        return 1
    except ValueError as err:
        print(f"the answer was JSON but not a recipe: {err}", file=sys.stderr)
        return 1
    except APIError as err:
        print(f"failed: {err.status_code} {err.code} {err.request_id}", file=sys.stderr)
        return 1
    print(f"{recipe.name} ({recipe.minutes} min): {', '.join(recipe.ingredients)}")
    return 0


def main() -> int:
    if not os.environ.get("MATTERWAVE_API_KEY"):
        print("Set MATTERWAVE_API_KEY to a MatterWave API key.", file=sys.stderr)
        return 2
    with MatterWave() as client:
        return run(client, " ".join(sys.argv[1:]) or "pancakes")


if __name__ == "__main__":
    sys.exit(main())
