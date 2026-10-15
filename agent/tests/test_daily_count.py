"""Daily order-counter serialization tests with no broker transport."""

from __future__ import annotations

import os
import json
import subprocess
import sys
from pathlib import Path

import pytest

import src.markets.live.paths as live_paths
from src.markets.live import daily_count
from src.markets.live.daily_count import (
    DailyCountError,
    daily_order_lock,
    increment_daily_count,
    read_daily_count,
)

pytestmark = pytest.mark.unit


def _child_lock_attempt(repo_root: Path, home: Path) -> subprocess.CompletedProcess[str]:
    script = """
from src.markets.live.daily_count import DailyOrderLockUnavailable, daily_order_lock
try:
    with daily_order_lock("alpaca"):
        print("acquired")
except DailyOrderLockUnavailable:
    print("blocked")
"""
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)  # Windows Path.home()
    env["PYTHONPATH"] = str(repo_root / "agent")
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )


def test_daily_order_lock_is_cross_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lock held here denies another process, then becomes available."""
    runtime_root = tmp_path / ".vibe-trading"
    monkeypatch.setattr(live_paths, "get_runtime_root", lambda: runtime_root)
    repo_root = Path(__file__).resolve().parents[2]

    with daily_order_lock("alpaca"):
        blocked = _child_lock_attempt(repo_root, tmp_path)
    acquired = _child_lock_attempt(repo_root, tmp_path)

    assert blocked.stdout.strip() == "blocked"
    assert acquired.stdout.strip() == "acquired"


def test_action_id_increment_is_durable_and_deduplicated(tmp_path, monkeypatch) -> None:
    runtime_root = tmp_path / ".vibe-trading"
    monkeypatch.setattr(live_paths, "get_runtime_root", lambda: runtime_root)

    assert increment_daily_count("alpaca", action_id="act_one") == 1
    assert increment_daily_count("alpaca", action_id="act_one") == 1
    assert increment_daily_count("alpaca", action_id="act_two") == 2
    assert read_daily_count("alpaca") == 2
    payload = json.loads((runtime_root / "live" / "alpaca" / "trade_counter.json").read_text())
    assert payload["action_ids"] == ["act_one", "act_two"]


def test_action_id_deduplicates_after_utc_rollover(tmp_path, monkeypatch) -> None:
    runtime_root = tmp_path / ".vibe-trading"
    current_day = ["2026-08-25"]
    monkeypatch.setattr(live_paths, "get_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(daily_count, "_utc_today", lambda: current_day[0])

    assert increment_daily_count("alpaca", action_id="act_recovered") == 1
    current_day[0] = "2026-08-26"
    assert increment_daily_count("alpaca", action_id="act_recovered") == 0
    assert read_daily_count("alpaca") == 0
    assert increment_daily_count("alpaca", action_id="act_new") == 1
    payload = json.loads((runtime_root / "live" / "alpaca" / "trade_counter.json").read_text())
    assert payload == {
        "date": "2026-08-26",
        "count": 1,
        "action_ids": ["act_new"],
    }


@pytest.mark.parametrize(
    ("counter_date", "action_ids"),
    [
        ("not-a-date", ["act_recovered"]),
        ("9999-12-31", ["act_recovered"]),
        ("2026-08-25", ["x" * 129]),
    ],
)
def test_action_id_accounting_rejects_malformed_evidence(
    tmp_path, monkeypatch, counter_date, action_ids
) -> None:
    runtime_root = tmp_path / ".vibe-trading"
    monkeypatch.setattr(live_paths, "get_runtime_root", lambda: runtime_root)
    path = runtime_root / "live" / "alpaca" / "trade_counter.json"
    path.parent.mkdir(parents=True)
    original = json.dumps({"date": counter_date, "count": 1, "action_ids": action_ids})
    path.write_text(original, encoding="utf-8")

    with pytest.raises(DailyCountError):
        increment_daily_count("alpaca", action_id="act_recovered")

    assert path.read_text(encoding="utf-8") == original


def test_action_id_accounting_wraps_directory_failure(tmp_path, monkeypatch) -> None:
    runtime_root = tmp_path / ".vibe-trading"
    runtime_root.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(live_paths, "get_runtime_root", lambda: runtime_root)

    with pytest.raises(DailyCountError):
        increment_daily_count("alpaca", action_id="act_recovered")
