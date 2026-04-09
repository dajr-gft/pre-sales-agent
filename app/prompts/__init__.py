from __future__ import annotations

from pathlib import Path

_PROMPT_PATH = Path(__file__).parent / 'root_prompt.md'

if not _PROMPT_PATH.exists():
    raise FileNotFoundError(
        f"Root agent prompt not found at '{_PROMPT_PATH}'. "
        'Ensure prompts/root_prompt.md exists in the project.'
    )

ROOT_PROMPT: str = _PROMPT_PATH.read_text(encoding='utf-8')

__all__ = ['ROOT_PROMPT']
