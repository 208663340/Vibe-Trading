"""SessionService coordinates an attempt; AttemptRunner assembles the agent."""

from __future__ import annotations

import asyncio
from pathlib import Path

from src.runtime.session.attempt_runner import AttemptRunner
from src.runtime.session.events import EventBus
from src.runtime.session.models import Attempt
from src.runtime.session.service import SessionService
from src.runtime.session.store import SessionStore


class _DummyIndex:
    def index_session(self, session_id: str, title: str) -> None:
        del session_id, title

    def index_message(self, session_id: str, role: str, content: str) -> None:
        del session_id, role, content

    def bind_store(self, base_dir: Path) -> None:
        del base_dir


def test_send_path_delegates_assembly_to_attempt_runner(monkeypatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    async def execute(self, attempt, messages=None, **kwargs):
        del messages
        seen["host_is_service"] = isinstance(self.host, SessionService)
        seen["prompt"] = attempt.prompt
        seen["shell"] = kwargs["include_shell_tools"]
        return {"status": "success", "content": "ok"}

    monkeypatch.setattr("src.runtime.session.service.get_shared_index", lambda: _DummyIndex())
    monkeypatch.setattr(AttemptRunner, "execute", execute)
    service = SessionService(SessionStore(tmp_path / "sessions"), EventBus(), tmp_path / "runs")
    attempt = Attempt(session_id="session-1", prompt="compare AAPL")

    result = asyncio.run(service._run_with_agent(attempt, messages=[], include_shell_tools=True))

    assert result["status"] == "success"
    assert seen == {
        "host_is_service": True,
        "prompt": "compare AAPL",
        "shell": True,
    }
