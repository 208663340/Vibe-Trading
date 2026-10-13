"""Durability regression: RunStateStore._write_json must fsync.

A crash between write() and the kernel flushing its page cache can leave
state.json truncated or empty. _write_json now writes in binary mode and
fsyncs the file descriptor before closing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import src.core.state as state_mod
from src.core.state import RunStateStore
from tests.module_os_helpers import patch_module_os


def test_write_json_calls_fsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fsynced: list[int] = []
    real_fsync = state_mod.os.fsync

    def spy(fd: int) -> None:
        fsynced.append(fd)
        real_fsync(fd)

    patch_module_os(monkeypatch, state_mod, fsync=spy)

    target = tmp_path / "state.json"
    RunStateStore._write_json(target, {"status": "success"})

    assert fsynced, "fsync was not called on _write_json"
    assert json.loads(target.read_text(encoding="utf-8")) == {"status": "success"}


def test_write_json_roundtrips_unicode(tmp_path: Path) -> None:
    target = tmp_path / "req.json"
    RunStateStore._write_json(target, {"prompt": "买入 A 股"})
    assert json.loads(target.read_text(encoding="utf-8"))["prompt"] == "买入 A 股"


@pytest.mark.parametrize("fault", ["fsync", "replace"])
def test_failed_replacement_preserves_previous_state(tmp_path, monkeypatch, fault):
    target = tmp_path / "state.json"
    RunStateStore._write_json(target, {"status": "running"})
    def fail(*args):
        raise OSError("interrupted replacement")
    patch_module_os(monkeypatch, state_mod, **{fault: fail})
    with pytest.raises(OSError, match="interrupted replacement"):
        RunStateStore._write_json(target, {"status": "success"})
    assert json.loads(target.read_text()) == {"status": "running"}
    assert list(tmp_path.iterdir()) == [target]


def test_previous_record_stays_readable_until_atomic_replacement(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    RunStateStore._write_json(target, {"status": "running"})
    real_replace = state_mod.os.replace
    observed = []
    def replace(source, destination):
        observed.append(json.loads(target.read_text()))
        assert json.loads(Path(source).read_text()) == {"status": "success"}
        real_replace(source, destination)
    patch_module_os(monkeypatch, state_mod, replace=replace)
    RunStateStore._write_json(target, {"status": "success"})
    assert observed == [{"status": "running"}]
    assert json.loads(target.read_text()) == {"status": "success"}
