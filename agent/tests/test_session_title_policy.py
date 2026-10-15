"""Title wording is decided without the HTTP route or a live model."""

from src.runtime.session.title import (
    build_title_prompt,
    normalize_generated_title,
    should_keep_existing_title,
)


def test_manual_title_is_kept_and_auto_prefix_is_not() -> None:
    assert should_keep_existing_title("Renamed by hand", "first prompt")
    assert not should_keep_existing_title("", "first prompt")
    assert not should_keep_existing_title("first prompt", "first prompt")


def test_title_prompt_uses_the_first_exchange_only() -> None:
    prompt = build_title_prompt("  look at NVDA  ", "  momentum is firm  ")
    assert "User: look at NVDA" in prompt
    assert "Assistant: momentum is firm" in prompt
    assert prompt.startswith("Write a session title")
    user_only = build_title_prompt("look at NVDA", None)
    assert "Assistant:" not in user_only


def test_generated_title_drops_quotes_and_extra_lines() -> None:
    assert normalize_generated_title('"NVDA Review"\nmore') == "NVDA Review"
    assert normalize_generated_title("「标题」") == "标题"
    assert normalize_generated_title("") == ""
    assert len(normalize_generated_title("a" * 50)) == 40
