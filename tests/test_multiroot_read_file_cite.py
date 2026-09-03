"""P2 follow-up A: Cowork/Ops 多根 read_file 的 cite 钩子 (ADR-006).

P2 (PR #76/#77) 给单根 ``file_tools`` (code agent 用的) 接上了 P2 自动
cite 钩子，但 Cowork/Ops 的 ``_files`` capability 仍走 aisuite 的
多根 ``read_file``——没有 cite。本测试覆盖在 ``_files`` 路径上替换
aisuite 多根 read_file 后，cite 行为在多根场景下的契约。

契约 (P2 follow-up A):

- 多根模式下，``read_file`` 接受绝对路径，按任何根目录解析（与
  aisuite 的多根 ``read_file`` 一致）。
- 引用记录在文件**实际所在**的根目录之下（不是主根）——所以从
  一个额外的只读文件夹读出的引用仍然以正确的工作空间相对路径
  进入 source ledger。
- 路径解析在所有已知根目录之外则报错。
- 单根模式的行为不变 (向后兼容)。
"""

from __future__ import annotations

from core.agents.base import AgentContext
from core.catalog import expand
from core.roots import RootDir
from core.sources import KIND_LINES, SourceStore
from integrations.tools.files import file_tools
from integrations.tools.todo import TodoList


def _setup(tmp_path):
    """Create a 3-root layout: scratch/ (primary), ro/, rw/."""
    scratch = tmp_path / "scratch"
    ro = tmp_path / "ro"
    rw = tmp_path / "rw"
    for d in (scratch, ro, rw):
        d.mkdir()
    (scratch / "in_scratch.txt").write_text("scratch content", encoding="utf-8")
    (ro / "shared_doc.txt").write_text("read-only content", encoding="utf-8")
    (rw / "writable_doc.txt").write_text("writable content", encoding="utf-8")
    return scratch, ro, rw


# -- file_tools(roots=...) direct ------------------------------------------


def test_multiroot_reads_from_primary_by_absolute_path(tmp_path):
    """Reading an absolute path under the primary root (scratch) works
    the same as the single-root case."""
    scratch, _ro, _rw = _setup(tmp_path)
    tools = file_tools(str(scratch), roots=[RootDir(path=scratch, writable=True)])
    out = tools[0](path=str(scratch / "in_scratch.txt"))
    assert "error" not in out
    assert "scratch content" in out["content"]
    assert out["path"] == "in_scratch.txt"


def test_multiroot_reads_from_added_readonly_root(tmp_path):
    """Reading an absolute path under an added read-only root works
    (P2 follow-up A's main goal: citation/auto-cite across all roots)."""
    scratch, ro, _rw = _setup(tmp_path)
    tools = file_tools(
        str(scratch),
        roots=[
            RootDir(path=scratch, writable=True),
            RootDir(path=ro, writable=False),
        ],
    )
    out = tools[0](path=str(ro / "shared_doc.txt"))
    assert "error" not in out
    assert "read-only content" in out["content"]
    # Path in the result is relative to the matching root (ro/), not scratch.
    assert out["path"] == "shared_doc.txt"


def test_multiroot_reads_from_added_writable_root(tmp_path):
    scratch, _ro, rw = _setup(tmp_path)
    tools = file_tools(
        str(scratch),
        roots=[
            RootDir(path=scratch, writable=True),
            RootDir(path=rw, writable=True),
        ],
    )
    out = tools[0](path=str(rw / "writable_doc.txt"))
    assert "error" not in out
    assert "writable content" in out["content"]
    assert out["path"] == "writable_doc.txt"


def test_multiroot_rejects_path_outside_every_root(tmp_path):
    scratch, ro, _rw = _setup(tmp_path)
    tools = file_tools(
        str(scratch),
        roots=[
            RootDir(path=scratch, writable=True),
            RootDir(path=ro, writable=False),
        ],
    )
    out = tools[0](path="/etc/hosts")
    assert "error" in out
    assert "escapes every known root" in out["error"]


def test_multiroot_relative_path_resolves_against_primary(tmp_path):
    """A relative path (no leading slash) resolves against the primary
    root, even when other roots are in play (matches aisuite's behavior)."""
    scratch, ro, _rw = _setup(tmp_path)
    tools = file_tools(
        str(scratch),
        roots=[
            RootDir(path=scratch, writable=True),
            RootDir(path=ro, writable=False),
        ],
    )
    out = tools[0](path="in_scratch.txt")
    assert "error" not in out
    assert out["path"] == "in_scratch.txt"


# -- cite hook with multi-root --------------------------------------------


def test_citation_records_matching_root_as_workspace(tmp_path):
    """P2 follow-up A core contract: a read from an added root
    records the citation with ``workspace=`` set to the matching root
    (so the source ledger's location field is normalized relative to
    the folder the file actually lives in)."""
    scratch, ro, _rw = _setup(tmp_path)
    store = SourceStore(tmp_path / "sources.json", workspace=scratch)
    tools = file_tools(
        str(scratch),
        source_store=store,
        run_id="run-multi",
        roots=[
            RootDir(path=scratch, writable=True),
            RootDir(path=ro, writable=False),
        ],
    )
    out = tools[0](path=str(ro / "shared_doc.txt"))
    assert "error" not in out
    refs = store.list()
    assert len(refs) == 1
    ref = refs[0]
    # Location is normalized relative to the ro/ root (not scratch/).
    assert ref.location == "shared_doc.txt"
    assert ref.cited_ranges == [
        {
            "run_id": "run-multi",
            "ranges": [{"kind": KIND_LINES, "start": 1, "end": 1}],
        }
    ]


def test_citation_from_each_root_is_normalized_to_that_root(tmp_path):
    """Two reads from two different roots yield two SourceRefs, each
    normalized to its own root (the read from ro/ lives under ro/, the
    read from rw/ lives under rw/)."""
    scratch, ro, rw = _setup(tmp_path)
    store = SourceStore(tmp_path / "sources.json", workspace=scratch)
    tools = file_tools(
        str(scratch),
        source_store=store,
        run_id="run-multi-2",
        roots=[
            RootDir(path=scratch, writable=True),
            RootDir(path=ro, writable=False),
            RootDir(path=rw, writable=True),
        ],
    )
    tools[0](path=str(ro / "shared_doc.txt"))
    tools[0](path=str(rw / "writable_doc.txt"))

    refs = {r.location: r for r in store.list()}
    assert "shared_doc.txt" in refs
    assert "writable_doc.txt" in refs
    assert refs["shared_doc.txt"].cited_ranges[0]["run_id"] == "run-multi-2"
    assert refs["writable_doc.txt"].cited_ranges[0]["run_id"] == "run-multi-2"


def test_single_root_path_in_result_mode_unchanged(tmp_path):
    """Without ``roots=``, the reader is single-root and the path in
    the result is relative to that single workspace (the original
    contract is preserved)."""
    scratch = tmp_path / "single"
    scratch.mkdir()
    (scratch / "x.txt").write_text("hi", encoding="utf-8")
    tools = file_tools(str(scratch))  # no roots
    out = tools[0](path="x.txt")
    assert out["path"] == "x.txt"


# -- catalog: _files capability exposes our cite-aware read_file -----------


def test_files_capability_uses_our_multiroot_read_file(tmp_path):
    """The ``files`` capability (Cowork/Ops) must now expose OUR
    cite-aware read_file (not aisuite's), and ``read_file_lines`` from
    aisuite remains for its separate windowed-reader use case."""
    scratch, ro, rw = _setup(tmp_path)
    ctx = AgentContext(
        workspace=scratch,
        roots=[
            RootDir(path=scratch, writable=True),
            RootDir(path=ro, writable=False),
            RootDir(path=rw, writable=True),
        ],
        executor=object(),
        todo=TodoList(),
    )
    tools = expand(["files"], ctx)
    names = {getattr(t, "__name__", "") for t in tools}
    # Our read_file (not aisuite's) + read_file_lines (still aisuite).
    assert "read_file" in names
    assert "read_file_lines" in names
    # search_files is dropped (we have grep).
    assert "search_files" not in names


def test_files_capability_no_roots_falls_back_to_single_root(tmp_path):
    """When ``context.roots`` is None, ``_files`` builds a single-root
    reader (mirroring the legacy behavior — relative paths only)."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "a.txt").write_text("a", encoding="utf-8")
    ctx = AgentContext(
        workspace=scratch,
        roots=None,
        executor=object(),
        todo=TodoList(),
    )
    tools = expand(["files"], ctx)
    read = next(t for t in tools if getattr(t, "__name__", "") == "read_file")
    # Single-root: relative path, works.
    out = read(path="a.txt")
    assert "a" in out["content"]
    # And absolute path outside the workspace is rejected.
    out2 = read(path=str(tmp_path / "elsewhere.txt"))
    assert "escapes the workspace" in out2["error"]
