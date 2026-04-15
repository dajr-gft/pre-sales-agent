from __future__ import annotations

from datetime import date
from pathlib import Path

_PROMPT_PATH = Path(__file__).parent / "root_prompt.md"

if not _PROMPT_PATH.exists():
    raise FileNotFoundError(
        f"Root agent prompt not found at '{_PROMPT_PATH}'. "
        "Ensure prompts/root_prompt.md exists in the project."
    )

ROOT_PROMPT: str = _PROMPT_PATH.read_text(encoding="utf-8")


class _PreservingDict(dict):
    """Dict that preserves unknown {placeholders} during format_map."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def build_instruction(company_name: str) -> str:
    """Assemble the root agent instruction by injecting runtime variables."""
    variables = {
        "todays_date": date.today().strftime("%d/%m/%Y"),
        "company_name": company_name,
    }
    return ROOT_PROMPT.format_map(_PreservingDict(variables))


__all__ = ["ROOT_PROMPT", "build_instruction"]
