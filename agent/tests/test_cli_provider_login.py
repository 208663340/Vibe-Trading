from __future__ import annotations

from unittest.mock import patch

from src.interfaces.cli import _legacy


def test_provider_login_explains_docker_tty_requirement(capsys) -> None:
    """An EOF from the OAuth prompt should point Docker users to a TTY command."""

    with patch(
        "src.runtime.providers.openai_codex.login_openai_codex",
        side_effect=EOFError("EOF when reading a line"),
    ):
        result = _legacy.cmd_provider_login("openai-codex")

    assert result == _legacy.EXIT_RUN_FAILED
    output = capsys.readouterr().out
    assert "interactive terminal" in output
    assert "docker compose exec vibe-trading" in output
    assert "-it" in output
