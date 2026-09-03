"""Shared citation hook for file/connector readers (DELTA_BLUEPRINT §7.2 实用).

Every reader (text, PDF, XLSX, DOCX, chat import) should follow the same
contract on a successful read:

  1. capture the source (or reuse the existing ref by fingerprint) so the
     Source ledger knows which version of the file the run saw;
  2. attach a typed citation (lines / page / cells / message_id / custom) so
     the UI can scroll back to the exact spot the run read.

This module is the single chokepoint — readers call :func:`cite` and get
both side effects. The "best-effort" stance (never raise out of a tool call)
lives here, not in every reader; a single transient I/O error must not
break a successful file read just because the audit hook happened to
choke.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.sources import CitationRange

if TYPE_CHECKING:
    from core.sources import SourceStore


def cite(
    source_store: "SourceStore | None",
    run_id: str | None,
    target: str | Path,
    range_obj: CitationRange | dict[str, Any],
    *,
    workspace: str | Path | None = None,
) -> str | None:
    """Capture + cite one read in a single call. Returns the ref id, or None
    on a no-op (no source_store / no run_id) or a swallowed error.

    ``run_id`` resolution order:

    1. The explicit ``run_id`` argument (automation pre-allocates it).
    2. The active :mod:`core.runscope` (manual turns: the adapter sets the
       scope on every driven turn; tool code reading in a worker thread
       still sees the same scope thanks to ``asyncio.to_thread``'s context
       copy).

    `target` is the absolute path the reader actually opened (the
    ``SourceStore`` will hash its content and keep the location
    workspace-relative when possible). `workspace` overrides the store's
    default workspace when the read happens outside it (e.g. a connector
    file mounted elsewhere).
    """
    if source_store is None:
        return None
    if not run_id:
        # Fall back to the ambient run scope — manual turns allocate the
        # run id on the first message, so the engine-built ``run_id`` is
        # None at the time the registry is wired. The adapter sets the
        # scope before driving each turn, so worker threads see it.
        from core.runscope import current as current_scope

        scope = current_scope()
        if scope is None:
            return None
        run_id = scope[0]
    try:
        ref = source_store.capture_file(target, workspace=workspace)
    except OSError:
        # A file that was readable a moment ago may have moved; the read
        # call already returned its result, so we just drop the audit
        # row rather than fail the tool.
        return None
    try:
        source_store.add_citation(ref.id, run_id, range_obj)
    except ValueError:
        # The reader produced a malformed range; drop the citation rather
        # than tear down the tool. The bug still gets caught by the
        # CitationRange unit tests + the e2e citation regression.
        return None
    return ref.id
