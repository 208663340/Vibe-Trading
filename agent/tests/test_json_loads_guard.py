"""Regression: _json_loads must not crash on corrupted JSON in database columns.

Both goal/store.py and strategy_store/sqlite_store.py define _json_loads
to deserialize JSON columns (theme, benchmark, assumptions, etc.). The
original code called json.loads(value) without a try/except — a
corrupted JSON string in the database (manual edit, partial write,
encoding issue) crashes the caller with json.JSONDecodeError, taking
down the entire goal/strategy read path.
"""

from __future__ import annotations

from src.runtime.goal.store import _json_loads as goal_json_loads
from src.research.strategy_store.sqlite_store import _json_loads as strategy_json_loads


def test_goal_json_loads_valid_json() -> None:
    assert goal_json_loads('["a", "b"]', []) == ["a", "b"]
    assert goal_json_loads('{"key": "val"}', {}) == {"key": "val"}


def test_goal_json_loads_empty_returns_default() -> None:
    assert goal_json_loads(None, []) == []
    assert goal_json_loads("", {}) == {}
    assert goal_json_loads("", "default") == "default"


def test_goal_json_loads_corrupted_returns_default() -> None:
    assert goal_json_loads("not json", []) == []
    assert goal_json_loads("{broken", {}) == {}
    assert goal_json_loads('["unclosed', []) == []


def test_strategy_json_loads_valid_json() -> None:
    assert strategy_json_loads('["momentum", "reversal"]', []) == ["momentum", "reversal"]


def test_strategy_json_loads_empty_returns_default() -> None:
    assert strategy_json_loads(None, []) == []
    assert strategy_json_loads("", []) == []


def test_strategy_json_loads_corrupted_returns_default() -> None:
    assert strategy_json_loads("not json", []) == []
    assert strategy_json_loads("{broken", []) == []
    assert strategy_json_loads('["unclosed', []) == []


def test_json_loads_non_string_input_returns_default() -> None:
    """Non-string input (e.g. int from a misconfigured column) must not crash."""
    assert goal_json_loads(123, []) == []  # type: ignore[arg-type]
    assert strategy_json_loads(123, []) == []  # type: ignore[arg-type]
