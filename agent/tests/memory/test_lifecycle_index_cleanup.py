"""GC and compression must keep the FTS index and semantic links in sync.

PersistentMemory.remove()/remove_entry() clean up the shared FTS index and
the .relations.json sidecar on every removal (see
TestFtsRemoveCleansIndex.test_fts_remove_cleans_index). MemoryLifecycle's own
write paths, garbage collection and compression, went through a completely
separate route (direct file rename/unlink, or an in-place rewrite) that never
touched either secondary index:

- A GC'd entry disappears from _scan_entries() (archive/ is not scanned), but
  its FTS row and .relations.json sidecar stayed behind indefinitely. A
  search whose top-N window includes that stale row silently returns fewer,
  less relevant results than it should, since find_relevant() drops any FTS
  match whose id is not in the live entry map without backfilling the next
  real candidate.
- Compression rewrites an entry's body on disk but never re-indexed the new
  text, so FTS ranking and snippets stayed computed against the
  pre-compression body indefinitely.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.platform.config.accessor import reset_env_config
from src.runtime.memory.lifecycle import MemoryLifecycle
from src.runtime.memory.persistent import PersistentMemory


@pytest.fixture(autouse=True)
def _reset_fts_singleton():
    """Reset FTS singleton between tests to avoid cross-test contamination."""
    import src.runtime.memory.search_index as si

    original = si._shared_index
    si._shared_index = None
    yield
    if si._shared_index is not None:
        try:
            si._shared_index.close()
        except Exception:
            pass
    si._shared_index = original


@pytest.fixture()
def fts_db(tmp_path, monkeypatch):
    """Redirect FTS singleton to a temporary database."""
    import src.runtime.memory.search_index as si

    db_path = tmp_path / "test_fts.db"
    monkeypatch.setattr(si, "_DEFAULT_DB_PATH", db_path)
    return db_path


def _create_memory_file(
    tmp_path: Path,
    name: str,
    content: str = "test body",
    memory_type: str = "project",
    quality_score: float = 0.01,
    access_count: int = 0,
    keywords: list | None = None,
    created_at: str | None = None,
    last_accessed: str | None = None,
    entry_id: str = "ab12cd",
) -> Path:
    """Write a memory file old and unimportant enough for GC to act on it."""
    old_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 400 * 86400))
    created_at = created_at or old_iso
    last_accessed = last_accessed or old_iso
    kw_str = ", ".join(keywords) if keywords else ""
    slug = name.lower().replace(" ", "_")[:40]
    filename = f"{memory_type}_{slug}.md"
    path = tmp_path / filename
    frontmatter = (
        f"---\n"
        f"name: {name}\n"
        f"description: {name}\n"
        f"type: {memory_type}\n"
        f"id: {entry_id}\n"
        f"created_at: {created_at}\n"
        f"updated_at: {created_at}\n"
        f"keywords: [{kw_str}]\n"
        f"quality_score: {quality_score}\n"
        f"access_count: {access_count}\n"
        f"last_accessed: {last_accessed}\n"
        f"---\n\n"
        f"{content}"
    )
    path.write_text(frontmatter, encoding="utf-8")
    return path


class TestGcCleansSecondaryIndexes:
    def test_gc_archive_removes_stale_fts_row(self, tmp_path: Path, monkeypatch, fts_db) -> None:
        """A GC'd entry's FTS row must not keep ranking (and displacing a
        real match) after the entry itself is unreachable via _scan_entries()."""
        monkeypatch.setenv("VT_MEMORY_GC", "1")
        monkeypatch.setenv("VT_MEMORY_DECAY", "1")
        monkeypatch.setenv("VT_MEMORY_FTS_INDEX", "true")
        reset_env_config()

        _create_memory_file(
            tmp_path,
            "ghost entry",
            content="unique-marker-token content here",
            entry_id="ghost1",
        )
        pm = PersistentMemory(memory_dir=tmp_path)
        entries = pm._scan_entries()
        from src.runtime.memory.search_index import get_shared_index

        index = get_shared_index()
        index.rebuild_all([(e.id, e.title, e.description, "", e.body) for e in entries])
        assert index.search("unique-marker-token", max_results=5), "sanity: entry is indexed before GC"

        lc = MemoryLifecycle(pm)
        actions = lc.run_gc(dry_run=False)
        assert actions and actions[0]["action"] == "archive"
        assert not (tmp_path / "project_ghost_entry.md").exists()

        matches = index.search("unique-marker-token", max_results=5)
        assert matches == [], f"GC left a stale FTS row behind for an archived entry: {matches}"

    def test_gc_archive_removes_relations_sidecar(self, tmp_path: Path, monkeypatch) -> None:
        """A GC'd entry's .relations.json sidecar must not survive it."""
        monkeypatch.setenv("VT_MEMORY_GC", "1")
        monkeypatch.setenv("VT_MEMORY_DECAY", "1")
        monkeypatch.setenv("VT_MEMORY_LINKS", "1")
        reset_env_config()

        path = _create_memory_file(tmp_path, "linked entry", content="content with relations")
        rel_path = path.parent / f"{path.stem}.relations.json"
        rel_path.write_text('[["abc123", 0.5]]', encoding="utf-8")

        pm = PersistentMemory(memory_dir=tmp_path)
        lc = MemoryLifecycle(pm)
        lc.run_gc(dry_run=False)

        assert not rel_path.exists(), "GC left an orphaned .relations.json sidecar behind"


class TestCompressionReindexesFts:
    def test_compression_updates_fts_body(self, tmp_path: Path, monkeypatch, fts_db) -> None:
        """After compression, FTS search must reflect the compressed text,
        not the pre-compression body that is no longer on disk."""
        monkeypatch.setenv("VT_MEMORY_GC", "1")
        monkeypatch.setenv("VT_MEMORY_COMPRESSION", "1")
        monkeypatch.setenv("VT_MEMORY_FTS_INDEX", "true")
        reset_env_config()

        # Younger than MIN_AGE_DAYS (Tier 1 skips it) but last_accessed older
        # than the daily-compression threshold (Tier 2 compresses it), same
        # setup as test_memory_gc.py::test_gc_execute_applies_compression.
        now = time.time()
        created_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now - 2 * 86400))
        accessed_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now - 9 * 86400))
        # Each sentence carries a single-token marker unique to it (not
        # "word {i}" split across two FTS tokens — _sanitize_fts_query joins
        # multi-token queries with OR, so a two-word query would match any
        # row containing either word and prove nothing about which sentences
        # survived compression).
        body = " ".join(f"Sentence uniquemarker{i} discusses trading details at length." for i in range(12))
        path = _create_memory_file(
            tmp_path,
            "aged raw entry",
            content=body,
            keywords=["alpha", "momentum"],
            created_at=created_iso,
            last_accessed=accessed_iso,
            entry_id="aged01",
        )

        pm = PersistentMemory(memory_dir=tmp_path)
        entries = pm._scan_entries()
        from src.runtime.memory.search_index import get_shared_index

        index = get_shared_index()
        index.rebuild_all([(e.id, e.title, e.description, "", e.body) for e in entries])
        assert index.search("uniquemarker8", max_results=5), "sanity: entry is indexed before compression"

        lc = MemoryLifecycle(pm)
        lc.run_gc(dry_run=False)

        after_text = path.read_text(encoding="utf-8")
        assert "compression_level: daily" in after_text, "entry did not actually compress"
        assert "uniquemarker8" not in after_text, (
            "sanity: the TF-IDF summarizer must actually drop sentence 8 for " "this test to prove anything"
        )

        matches = index.search("uniquemarker8", max_results=5)
        assert matches == [], (
            "FTS index still matches text compression already dropped from the " f"entry body: {matches}"
        )
