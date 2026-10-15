"""Atomic JSON durability checks for filesystem-backed sessions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.runtime.session.store import SessionStore


def test_atomic_json_write_roundtrips_unicode(tmp_path: Path) -> None:
    target = tmp_path / "attempt.json"
    SessionStore._write_json(target, {"summary": "恢复中的回复"})
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "summary": "恢复中的回复"
    }
    assert list(tmp_path.glob(".attempt.json.*.tmp")) == []


def test_failed_atomic_replace_preserves_previous_json(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "session.json"
    SessionStore._write_json(target, {"status": "old"})

    def fail_replace(source, destination) -> None:
        del source, destination
        raise OSError("simulated replace failure")

    monkeypatch.setattr("src.runtime.session.store.os.replace", fail_replace)
    with pytest.raises(OSError, match="replace failure"):
        SessionStore._write_json(target, {"status": "new"})

    assert json.loads(target.read_text(encoding="utf-8")) == {"status": "old"}
    assert list(tmp_path.glob(".session.json.*.tmp")) == []
