"""The Windows lock path must not write a sentinel byte into an empty file.

`msvcrt.locking` locks a byte RANGE, and on Windows a range beyond EOF is a
valid lock target — so an empty file can be locked at offset 0 without writing
anything. The previous implementation seeked to EOF and wrote ``b"\\0"`` first
to give itself a byte to lock. For the compliance ledger that sentinel is a
malformed line 0, so `_walk_chain` raised `LedgerCorruptionError` on the very
first append and the ledger was unusable on Windows.

These tests exist because that path is unreachable on POSIX: `msvcrt` is None
here, so the branch never runs and a regression would be invisible to every
non-Windows contributor and to the Linux and macOS CI jobs. Rather than trust
a reviewer's Windows run, the module-level `fcntl` / `msvcrt` handles are
swapped for stubs so the Windows branch executes on every platform.

The assertions are deliberately behavioural, not "was locking() called":
a test that only checks the call would stay green if the sentinel came back.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import src.platform.governance.ledger as ledger_mod
import src.markets.live.daily_count as daily_count_mod
import src.runtime.providers.openai_codex as codex_mod
from src.platform.governance.ledger import append_record, verify_chain


class _StubMsvcrt:
    """Records lock calls; never touches a real Windows lock."""

    LK_LOCK = 1
    LK_NBLCK = 2
    LK_UNLCK = 0

    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def locking(self, fd: int, mode: int, nbytes: int) -> None:
        self.calls.append((mode, nbytes))


@pytest.fixture()
def windows_locks(monkeypatch: pytest.MonkeyPatch) -> _StubMsvcrt:
    """Force every lock helper down its Windows branch on any platform."""
    stub = _StubMsvcrt()
    for module in (ledger_mod, daily_count_mod, codex_mod):
        monkeypatch.setattr(module, "fcntl", None, raising=False)
        monkeypatch.setattr(module, "msvcrt", stub, raising=False)
    return stub


def test_first_ledger_append_succeeds_on_the_windows_path(
    tmp_path: Path, windows_locks: _StubMsvcrt
) -> None:
    """The regression itself: a sentinel byte makes line 0 malformed.

    With the sentinel, this raised LedgerCorruptionError on the first append
    and the compliance ledger could never be started on Windows.
    """
    path = tmp_path / "audit.jsonl"

    first = append_record(path, {"event": "first"}, fsync=False)
    second = append_record(path, {"event": "second"}, fsync=False)

    assert first["seq"] == 1
    assert first["prev_record_hash"] == ledger_mod.GENESIS_PREV_HASH
    assert second["prev_record_hash"] == first["record_hash"]

    result = verify_chain(path)
    assert result.ok is True, result.first_break
    assert result.record_count == 2
    assert windows_locks.calls, "the Windows lock branch did not execute"


def test_locking_an_empty_file_writes_nothing(
    tmp_path: Path, windows_locks: _StubMsvcrt
) -> None:
    """Locking must leave a zero-byte file zero-byte.

    This is the property the ledger test depends on, asserted directly so a
    regression names the cause rather than only its downstream symptom.
    """
    path = tmp_path / "empty.bin"
    path.touch()
    assert path.stat().st_size == 0

    with path.open("r+b") as handle:
        ledger_mod._lock_exclusive(handle)
        ledger_mod._unlock(handle)

    assert path.stat().st_size == 0, "the lock wrote a sentinel byte"


def test_daily_count_and_codex_locks_also_write_nothing(
    tmp_path: Path, windows_locks: _StubMsvcrt
) -> None:
    """The same fix landed in three places; pin all three.

    ``daily_count`` gates the mandate's trades-per-day cap and
    ``openai_codex`` guards a cached token file — a stray byte in either is a
    parse failure on the next read, not a cosmetic difference.
    """
    for name, locker in (
        ("daily.json", daily_count_mod._try_lock),
        ("codex_token.json", codex_mod._lock_token_file),
    ):
        path = tmp_path / name
        path.touch()
        with path.open("r+b") as handle:
            locker(handle)
        assert path.stat().st_size == 0, f"{name}: the lock wrote a sentinel byte"


def test_locks_target_offset_zero(
    tmp_path: Path, windows_locks: _StubMsvcrt
) -> None:
    """A one-byte range at offset 0 is what makes the beyond-EOF lock valid."""
    path = tmp_path / "offset.bin"
    path.touch()

    with path.open("r+b") as handle:
        handle.seek(64)  # a stale position must not become the lock offset
        ledger_mod._lock_exclusive(handle)
        assert handle.tell() == 0, "lock did not seek to offset 0 first"
        ledger_mod._unlock(handle)

    assert [nbytes for _, nbytes in windows_locks.calls] == [1, 1]
