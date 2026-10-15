"""Session title wording, separate from the HTTP route.

The route decides whether a rename is allowed and maps failures to status
codes. This module builds the prompt, normalises the model text, and performs
the single provider call.
"""

from __future__ import annotations


def should_keep_existing_title(current: str, auto_prefix: str) -> bool:
    """A manual title stays. An empty title, or the first-prompt prefix, may be replaced."""
    return bool(current) and current != auto_prefix


def build_title_prompt(user_text: str, assistant_text: str | None) -> str:
    excerpt = f"User: {user_text.strip()[:600]}"
    if assistant_text and assistant_text.strip():
        excerpt += f"\nAssistant: {assistant_text.strip()[:600]}"
    return (
        "Write a session title for the conversation below: at most 8 words "
        "(or 16 CJK characters), in the same language as the user's message, "
        "no quotes, no trailing punctuation. Reply with the title only.\n\n"
        + excerpt
    )


def normalize_generated_title(raw: str) -> str:
    title = raw.splitlines()[0].strip().strip("\"'“”「」『』").strip() if raw else ""
    chars = list(title)
    if len(chars) > 40:
        title = "".join(chars[:40])
    return title


def request_session_title(prompt: str) -> str:
    """Ask the configured chat model for a title and always close the client."""
    from src.runtime.providers.chat import ChatLLM

    llm = ChatLLM()
    try:
        response = llm.chat([{"role": "user", "content": prompt}], timeout=30)
        return (getattr(response, "content", "") or "").strip()
    finally:
        llm.close()
