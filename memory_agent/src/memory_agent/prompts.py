"""Prompt construction for the shared OpenCode-backed memory-agent session."""

from __future__ import annotations

from .config import Settings


def recall_prompt(input_text: str, settings: Settings) -> str:
    _ = settings
    return f"""Task: recall memory for the request below.
Return only the useful recalled context, or state briefly that no relevant memory was found.

Request:
{input_text}
"""


def remember_prompt(input_text: str, settings: Settings) -> str:
    _ = settings
    return f"""Task: remember durable information from the input below if it passes the memory rules.
If nothing should be stored or updated, do nothing.

Input:
{input_text}
"""


def rem_sleep_prompt(settings: Settings) -> str:
    _ = settings
    return """Task: REM sleep maintenance request.
Delegate the actual maintenance to `klona-rem-sleep`. Pass only this short instruction: "Do REM sleep following your instruction".
Return the REM sleep summary as-is or briefly.
"""
