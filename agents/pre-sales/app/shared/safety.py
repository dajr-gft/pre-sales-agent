"""Centralized Gemini safety settings, reused by root and sub-agents."""

from __future__ import annotations

from google.genai import types

from ..config import config


def build_safety_settings() -> list[types.SafetySetting]:
    """Build the per-category safety settings list from config.

    Returns a fresh list each call so a caller can append/override without
    mutating settings used by other agents.
    """
    threshold = types.HarmBlockThreshold(config.SAFETY_HARM_BLOCK_THRESHOLD)
    categories = (
        types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
    )
    return [
        types.SafetySetting(category=cat, threshold=threshold)
        for cat in categories
    ]
