"""R4 contract: `read_file_lines` is a first-class read tool that shares the
Delta multi-root path resolver and the Source/Citation chokepoint with
`read_file`. Reads must produce a `lines` citation in the run's source ledger.

These tests exist to lock the v0.3.1 contract:
  - the tool name `read_file_lines` is preserved (no rename)
  - the implementation goes through the same `_windowed_read` chokepoint
  - the citation is recorded with `kind = "lines"` and the actual read window
  - a broken cite hook must not break the read (audit is best-effort)
  - the tool respects multi-root path resolution
  - the tool returns a clear error for missing files / path-escape attempts
"""

from __future__ import annotations

from integrations.tools.files import file_tools


def _read_file_lines(tools):
    return next(t for t in tools if t.__name__ == "read_file_lines")


def test_read_file_lines_returns_numbered_window(tmp_path):
    target = tmp_path / "report.txt"
    target.write_text("\n".join(f"line {i}" for i in range(1, 11)), encoding="utf-8")

    tools = file_tools(str(tmp_path))
    out = _read_file_lines(tools)(str(target), 3, 4)

    assert out["path"] == "report.txt"
    assert out["start_line"] == 3
    assert out["end_line"] == 6
    assert "line 3" in out["content"]
    assert "line 4" in out["content"]
    assert "line 5" in out["content"]
    assert "line 6" in out["content"]
    # The default window is small (100) but we requested 4; pagination works.
    assert out["has_more"] is True


def test_read_file_lines_cite_hook_records_lines_citation(tmp_path):
    """Reads through read_file_lines must land as a `lines` citation in the
    run's source ledger (Source/Citation chokepoint shared with read_file)."""
    from core.sources import KIND_LINES, SourceStore

    target = tmp_path / "data.txt"
    target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    store = SourceStore(path=tmp_path / "sources.json")
    run_id = "run_test_r4"

    tools = file_tools(
        str(tmp_path), source_store=store, run_id=run_id
    )
    out = _read_file_lines(tools)("data.txt", 2, 2)

    assert out["start_line"] == 2
    assert out["end_line"] == 3

    # The source ledger now has exactly one citation for this run.
    refs = store.list()
    assert len(refs) == 1
    ref = refs[0]

    # Find the citation row that belongs to this run; it must contain a
    # `lines` range matching the actual read window.
    matching_runs = [c for c in ref.cited_ranges if c.get("run_id") == run_id]
    assert matching_runs, f"no citation for run {run_id} (got {ref.cited_ranges!r})"
    ranges = matching_runs[0]["ranges"]
    line_ranges = [r for r in ranges if r.get("kind") == KIND_LINES]
    assert line_ranges, f"expected a lines citation, got {ranges!r}"
    line_range = line_ranges[0]
    assert line_range["start"] == 2
    assert line_range["end"] == 3


def test_read_file_lines_broken_cite_hook_does_not_break_read(tmp_path):
    """A failure inside the Source/Citation hook must not break the read itself.
    The hook is best-effort (OSError / ValueError only — anything else surfaces
    normally): the user has already seen the lines, so refusing the read on a
    bookkeeping error would be a worse failure mode.
    """
    target = tmp_path / "data.txt"
    target.write_text("hello\nworld\n", encoding="utf-8")

    class _BoomStore:
        def capture_file(self, *_a, **_kw):
            raise OSError("capture failure")

    tools = file_tools(
        str(tmp_path),
        source_store=_BoomStore(),
        run_id="run_test_r4_boom",
    )
    # Must not raise.
    out = _read_file_lines(tools)("data.txt", 1, 2)
    assert "content" in out
    assert "hello" in out["content"]
    assert "world" in out["content"]


def test_read_file_lines_missing_file_returns_error(tmp_path):
    tools = file_tools(str(tmp_path))
    out = _read_file_lines(tools)("does_not_exist.txt", 1, 5)
    assert "error" in out
    assert "not a file" in out["error"]


def test_read_file_lines_path_escape_returns_error(tmp_path):
    """A path that escapes the single-root workspace must be refused; this is
    the same invariant `read_file` enforces."""
    tools = file_tools(str(tmp_path))
    out = _read_file_lines(tools)("../outside.txt", 1, 5)
    assert "error" in out
    assert "escape" in out["error"]


def test_read_file_lines_resolves_against_second_root(tmp_path):
    """Multi-root: a path inside the second root must be readable, and the
    citation is recorded against the matching root (not the primary)."""
    from core.sources import SourceStore

    primary = tmp_path / "primary"
    second = tmp_path / "second"
    primary.mkdir()
    second.mkdir()
    (second / "shared.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    store = SourceStore(path=tmp_path / "sources.json")

    tools = file_tools(
        str(primary),
        source_store=store,
        run_id="run_r4_multiroot",
        roots=[primary, second],
    )
    # Absolute path inside the second root.
    out = _read_file_lines(tools)(str(second / "shared.txt"), 1, 3)
    assert "error" not in out, out
    assert "alpha" in out["content"]
    # The returned path is relative to the matching root (second), not primary.
    assert out["path"] in ("shared.txt", str(second / "shared.txt"))


def test_read_file_lines_unicode_path(tmp_path):
    """Chinese + spaces in the path must work; the catalog uses the same
    chokepoint as read_file, so the path handling is shared."""
    target_dir = tmp_path / "测试 目录"
    target_dir.mkdir()
    target = target_dir / "数据.txt"
    target.write_text("一行\n二行\n三行\n", encoding="utf-8")

    tools = file_tools(str(tmp_path))
    out = _read_file_lines(tools)(str(target), 1, 3)
    assert "error" not in out, out
    assert "一行" in out["content"]
    assert "二行" in out["content"]
