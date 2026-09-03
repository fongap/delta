"""P3 §7.3 Source 完整能力 — 索引失效检测 (mtime fast path).

``check_freshness`` 现在先比对 ``(mtime_ns, size_bytes)`` — 相同则
直接判定 ``current``，根本不算 sha256。变了 (或 ref 缓存缺失) 才
走 sha256 兜底。

设计契约:

- ``capture_file`` 记录 mtime_ns + size_bytes 在 ref 上 (P3 §7.3)
- ``check_freshness`` / ``reindex_stale`` 用 mtime fast path
- mtime + size 都没变 → 0 次 read_bytes (即使文件 1MB+)
- mtime 或 size 变了 → 走 sha256 兜底
- 旧 ref (无 mtime/size 缓存) → 总是走 sha256 (向后兼容)
- ``reindex_stale(force=True)`` 跳过 fast path, 强制重算
- 行为变更: ``check_freshness`` 在 ref 已 current + mtime 已缓存时
  顺便刷新 mtime 缓存 (后续 pass 继续走快路径)
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from core.sources import (
    FRESH_CHANGED,
    FRESH_CURRENT,
    FRESH_MISSING,
    SourceStore,
)


def _store_with_ref(tmp_path: Path, content: bytes = b"hello"):
    p = tmp_path / "x.txt"
    p.write_bytes(content)
    store = SourceStore(tmp_path / "s.json", workspace=tmp_path)
    ref = store.capture_file(p)
    return p, store, ref


# -- capture_file records mtime_ns + size_bytes --------------------------


def test_capture_records_mtime_and_size(tmp_path):
    p, store, ref = _store_with_ref(tmp_path, b"hello")
    assert ref.mtime_ns is not None
    assert ref.size_bytes == 5


def test_capture_outside_workspace_records_mtime_too(tmp_path):
    """Absolute-path capture also caches the stat (the cache is
    per-ref, not per-workspace)."""
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"data")
    store = SourceStore(tmp_path / "s.json", workspace=tmp_path)
    ref = store.capture_file(outside)
    assert ref.mtime_ns is not None
    assert ref.size_bytes == 4


# -- check_freshness mtime fast path -------------------------------------


def test_check_freshness_skips_sha256_when_unchanged(tmp_path):
    """A no-op reindex of an unchanged file should not read the file
    at all (the optimization under test). We patch ``read_bytes`` and
    assert it's never called."""
    p, store, _ = _store_with_ref(tmp_path, b"x" * 1_000_000)
    original = Path.read_bytes
    calls: list[Path] = []

    def counting(self, *a, **kw):
        calls.append(self)
        return original(self, *a, **kw)

    with patch.object(Path, "read_bytes", counting):
        store.check_freshness()
    assert calls == [], f"expected 0 reads, got {len(calls)}"


def test_check_freshness_detects_change_via_sha_when_mtime_or_size_differs(
    tmp_path,
):
    """When the file size or mtime changes, the fast path falls
    through to sha256 and reports drift."""
    p, store, ref = _store_with_ref(tmp_path, b"old")
    p.write_bytes(b"new content")  # 3 → 11 bytes, mtime bumps
    drifted = store.check_freshness()
    assert ref.id in [r.id for r in drifted]
    assert store.get(ref.id).status == FRESH_CHANGED


def test_check_freshness_detects_missing_file(tmp_path):
    p, store, ref = _store_with_ref(tmp_path)
    p.unlink()
    drifted = store.check_freshness()
    assert store.get(ref.id).status == FRESH_MISSING
    assert ref.id in [r.id for r in drifted]


# -- reindex_stale (P3 §7.3 new entry point) ----------------------------


def test_reindex_stale_skips_sha256_on_stable_files(tmp_path):
    p, store, _ = _store_with_ref(tmp_path, b"x" * 1_000_000)
    original = Path.read_bytes
    calls: list[Path] = []

    def counting(self, *a, **kw):
        calls.append(self)
        return original(self, *a, **kw)

    with patch.object(Path, "read_bytes", counting):
        store.reindex_stale()
    assert calls == []


def test_reindex_stale_only_returns_actually_drifted_refs(tmp_path):
    p_stable = tmp_path / "stable.txt"
    p_changed = tmp_path / "changed.txt"
    p_missing = tmp_path / "missing.txt"
    p_stable.write_bytes(b"keep")
    p_changed.write_bytes(b"v1")
    p_missing.write_bytes(b"gone")
    store = SourceStore(tmp_path / "s.json", workspace=tmp_path)
    for p in (p_stable, p_changed, p_missing):
        store.capture_file(p)
    p_changed.write_bytes(b"v2 longer")  # mtime + size change
    p_missing.unlink()
    drifted = store.reindex_stale()
    drifted_paths = {r.location for r in drifted}
    assert drifted_paths == {"changed.txt", "missing.txt"}


def test_reindex_stale_force_skips_mtime_fast_path(tmp_path):
    """``force=True`` always rehashes, even when mtime+size are
    unchanged. Use case: a ``git checkout`` that restored a file's
    mtime but with stale content (rare but possible)."""
    p, store, _ = _store_with_ref(tmp_path, b"x" * 1_000_000)
    original = Path.read_bytes
    calls: list[Path] = []

    def counting(self, *a, **kw):
        calls.append(self)
        return original(self, *a, **kw)

    with patch.object(Path, "read_bytes", counting):
        store.reindex_stale(force=True)
    # Force bypasses the fast path; the file is read once.
    assert len(calls) == 1


def test_reindex_stale_refreshes_cached_mtime_on_current_pass(tmp_path):
    """A successful reindex refreshes the cached mtime so the next
    pass can keep skipping the hash (matters when stat() granularity
    differs from capture's)."""
    p, store, ref = _store_with_ref(tmp_path)
    original_mtime = ref.mtime_ns
    # Bump mtime by 1ns (way under any filesystem granularity, but
    # we only need to verify the cache gets updated, not that the
    # underlying stat actually changed in storage).
    new_mtime = original_mtime + 1_000_000_000
    os.utime(p, ns=(new_mtime, new_mtime))
    # Force a pass — file content unchanged so the mtime-only bump
    # keeps status at current; the cache should be updated to the
    # post-utime mtime.
    store.reindex_stale()
    assert store.get(ref.id).status == FRESH_CURRENT
    assert store.get(ref.id).mtime_ns == new_mtime


# -- legacy refs (no mtime cache) still work ----------------------------


def test_legacy_ref_without_mtime_cache_always_hashes(tmp_path):
    """A ref saved by an older code path (no mtime_ns / size_bytes)
    must still be hashable — we fall through to sha256 every pass."""
    p, store, _ = _store_with_ref(tmp_path)
    ref = store.get(store.list()[0].id)
    # Simulate an older ref: clear the cache fields.
    ref.mtime_ns = None
    ref.size_bytes = None
    store._save()
    original = Path.read_bytes
    calls: list[Path] = []

    def counting(self, *a, **kw):
        calls.append(self)
        return original(self, *a, **kw)

    with patch.object(Path, "read_bytes", counting):
        store.check_freshness()
    # Legacy path: must hash every time (no fast path shortcut).
    assert len(calls) == 1
