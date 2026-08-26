"""Run scope — the ambient identity of the currently executing run.

The Run Event Ledger records facts per run (docs/run-ledger-adr.md), but tools and
the executor sit BELOW the runtime adapter that owns the run id. Instead of threading
`run_id` through build_engine → shell_tools → executor signatures, the adapter
publishes the active run into a context variable for the duration of each driven
turn. `asyncio.to_thread` copies contexts, so tool code executing in worker threads
reads the same scope without any signature changes.

Background tasks that outlive their spawning turn (and lifecycle kills on session or
app teardown) observe an empty scope — their recorders decide where such events land
(the manager routes them to the audit trail instead of the run ledger).
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Optional

Scope = tuple[str, str]  # (run_id, session_id)

_current: ContextVar[Scope | None] = ContextVar(
    "coworker_run_scope", default=None
)


def set_current(run_id: str, session_id: str = "") -> Token:
    """Publish the active run for the current context; returns the reset token."""
    return _current.set((run_id, session_id))


def reset(token: Token) -> None:
    _current.reset(token)


def current() -> Scope | None:
    """The active (run_id, session_id), or None outside any driven turn."""
    return _current.get()
