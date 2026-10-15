"""Shared store singleton for SDM tools.

All SDM tools (sdm_register, sdm_status, sdm_decay_scan) import
``get_store()`` from this module to share a single ``SqliteStrategyStore``
instance.  The SQLite backend provides WAL-mode persistence with FK
constraints and schema migrations via ``PRAGMA user_version``.
"""

from __future__ import annotations

from src.research.strategy_store.sqlite_store import SqliteStrategyStore

_store: SqliteStrategyStore | None = None


def get_store() -> SqliteStrategyStore:
    """Return the process-wide strategy store singleton."""
    global _store
    if _store is None:
        _store = SqliteStrategyStore()
    return _store
