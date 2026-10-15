"""One corrupt session.json must not abort SessionStore.list_sessions."""

from __future__ import annotations

import json
from pathlib import Path

from src.runtime.session.models import Session
from src.runtime.session.store import SessionStore


def test_list_sessions_skips_invalid_status_and_keeps_siblings(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    store.create_session(Session(session_id="goodsession01", title="good"))

    bad = store.base_dir / "badsession02"
    bad.mkdir()
    (bad / "session.json").write_text(
        json.dumps(
            {
                "session_id": "badsession02",
                "title": "broken",
                "status": "not-a-status",
                "created_at": "2020-01-01T00:00:00+00:00",
                "updated_at": "2020-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    listed = store.list_sessions()
    assert [s.session_id for s in listed] == ["goodsession01"]


def test_list_sessions_skips_non_object_session_json(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    store.create_session(Session(session_id="goodsession01", title="good"))

    bad = store.base_dir / "listsess"
    bad.mkdir()
    (bad / "session.json").write_text('"not-an-object"', encoding="utf-8")

    listed = store.list_sessions()
    assert [s.session_id for s in listed] == ["goodsession01"]
