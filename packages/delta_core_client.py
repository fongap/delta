"""Delta Core host client — Python interface to the delta-core Rust process.

R5.1 / AF-04/05/06: Multiplexed Runtime Protocol v16.

Instead of the v15 model (single lock held for entire command/stream), v16
uses a dedicated stdout reader thread + per-request routing:

* Each request gets a unique ``request_id``.
* The reader thread parses stdout lines, extracts ``request_id``, and routes
  each frame to the correct per-request queue.
* A writer lock guards a single JSON line write — never the whole command.
* A single client supports many concurrent in-flight requests and streams.
* ``request_cancel(request_id)`` sends ``request.cancel`` to interrupt a
  running stream without waiting for it to finish.

Public Contract (v16):

* :meth:`command` — send one request, wait for its response. Thread-safe.
* :meth:`stream` — send a streaming request, yield delta frames. Thread-safe.
* :meth:`stream_cancel` — cancel a running stream by ``request_id``.
* :meth:`close` — shut down the subprocess and fan-out errors to all
  pending requests and streams.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import queue
from pathlib import Path
from typing import Any, Generator

REPO_ROOT = Path(__file__).resolve().parent.parent
CRATE_DIR = REPO_ROOT / "core" / "runtime-native"

# Default command timeout in seconds.
DEFAULT_COMMAND_TIMEOUT_SECONDS: float = 30.0

# Delta Core wire-protocol version. Must match the
# ``PROTOCOL_VERSION`` constant in
# ``core/runtime-native/src/bin/delta_core.rs``.
PROTOCOL_VERSION: int = 16


def _find_delta_core_binary() -> Path | None:
    """Locate the delta_core binary in the standard search order."""
    target = "delta_core.exe" if sys.platform == "win32" else "delta_core"

    env = os.environ.get("DELTA_CORE_BINARY")
    if env and Path(env).exists():
        return Path(env)

    py_exe = Path(sys.executable).resolve()
    py_dir = py_exe.parent
    if (py_dir / target).exists():
        return py_dir / target

    for level in range(1, 4):
        ancestor = py_exe.parents[level] if len(py_exe.parents) > level else None
        if ancestor and (ancestor / target).exists():
            return ancestor / target

    portable_root = os.environ.get("DELTA_PORTABLE_ROOT")
    if portable_root:
        candidate = Path(portable_root) / "App" / "Delta" / target
        if candidate.exists():
            return candidate

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        meipass_path = Path(meipass) / target
        if meipass_path.exists():
            return meipass_path

    for profile in ("release", "debug"):
        candidate = CRATE_DIR / "target" / profile / target
        if candidate.exists():
            return candidate
    return None


class DeltaCoreError(RuntimeError):
    """Raised when the delta_core subprocess returns an error or dies."""


class DeltaCoreClient:
    """Persistent multiplexed client to the delta_core Rust process (v16).

    Internals:

    * ``_write_lock`` — a lock that protects a single ``stdin.write()`` call
      (not the whole command). Multiple threads can interleave writes.
    * ``_reader_thread`` — a dedicated thread that reads stdout lines and
      routes them by ``request_id`` to the correct pending request or
      stream queue.
    * ``_pending`` — ``{request_id: queue.Queue}`` for non-streaming responses.
    * ``_streams`` — ``{request_id: queue.Queue}`` for streaming frames.
    * ``_next_id`` — an atomic request_id counter.
    * ``_crash()`` — fans out a crash error to all pending requests and
      streams so no caller blocks forever.
    """

    def __init__(
        self,
        binary_path: Path | None = None,
        command_timeout: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ) -> None:
        if binary_path is None:
            binary_path = _find_delta_core_binary()
            if binary_path is None:
                raise DeltaCoreError(
                    "delta_core binary not found in any standard location; "
                    "set DELTA_CORE_BINARY or build core/runtime-native"
                )
        self._binary_path = binary_path
        self._command_timeout = command_timeout

        # AF-07: bounded inflight requests — admission control.
        # When this semaphore is exhausted, new commands raise
        # DeltaCoreError("overload") instead of queuing indefinitely.
        self._max_inflight = 64
        self._inflight = threading.Semaphore(self._max_inflight)

        self._proc: subprocess.Popen | None = None
        # RLock: _ensure_started → _handshake may call close() on failure,
        # and close() re-acquires this lock.
        self._proc_lock = threading.RLock()

        self._write_lock = threading.Lock()  # one line write at a time
        self._reader_thread: threading.Thread | None = None
        self._reader_stop = threading.Event()

        self._stderr_thread: threading.Thread | None = None
        self._stderr_stop = threading.Event()

        self._pending: dict[int, queue.Queue] = {}  # request_id → response queue
        self._streams: dict[int, queue.Queue] = {}  # request_id → stream queue
        self._pending_lock = threading.Lock()

        self._next_id = 0
        self._id_lock = threading.Lock()

        # AF-12/13: RunningTask registry — who spawned owns completion.
        self._running: dict[int, dict] = {}  # request_id → task metadata
        self._running_lock = threading.Lock()

        # Track request_ids that were assigned by the server when the
        # client did not send one (v15 compat path). Not used in v16.
        # We always send request_id in v16, so this is empty.
        self._server_assigned: set[int] = set()

    @staticmethod
    def _find_binary() -> Path | None:
        """Module-level binary lookup — used by delegates to check availability."""
        return _find_delta_core_binary()

    @property
    def binary_path(self) -> Path:
        return self._binary_path

    def _alloc_id(self) -> int:
        with self._id_lock:
            self._next_id += 1
            return self._next_id

    # -- subprocess lifecycle ---------------------------------------------------

    def _stderr_drainer(self, proc: subprocess.Popen) -> threading.Thread:
        """Start a background thread that drains ``proc.stderr``."""
        stop = self._stderr_stop

        def drain():
            try:
                stream = proc.stderr
                if stream is None:
                    return
                while not stop.is_set():
                    line = stream.readline()
                    if not line:
                        return
            except Exception:
                return

        thread = threading.Thread(
            target=drain, name="delta-core-stderr-drain", daemon=True
        )
        thread.start()
        return thread

    def _ensure_started(self) -> None:
        with self._proc_lock:
            if self._proc is not None and self._proc.poll() is None:
                return
            if not self._binary_path.exists():
                raise DeltaCoreError(
                    f"delta_core binary not built: {self._binary_path}"
                )
            self._proc = subprocess.Popen(
                [str(self._binary_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
            self._stderr_stop.clear()
            self._stderr_thread = self._stderr_drainer(self._proc)
            # Handshake BEFORE starting the stdout reader thread —
            # the reader would consume the handshake response otherwise.
            self._handshake()
            self._reader_stop.clear()
            self._reader_thread = self._stdout_reader(self._proc)

    def _handshake(self) -> None:
        """Send a ``hello`` command and verify the protocol version."""
        if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
            raise DeltaCoreError("delta_core subprocess not started")
        cmd = json.dumps({"cmd": "hello", "protocol_version": PROTOCOL_VERSION})
        try:
            self._proc.stdin.write(cmd + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            self._proc = None
            raise DeltaCoreError(f"delta_core handshake write failed: {e}") from e
        line = self._proc.stdout.readline()
        if not line:
            self._proc = None
            raise DeltaCoreError("delta_core handshake: empty response (subprocess died?)")
        try:
            resp = json.loads(line)
        except json.JSONDecodeError as e:
            self.close()
            raise DeltaCoreError(f"delta_core handshake: non-JSON response: {line!r}") from e
        if not resp.get("ok"):
            self.close()
            raise DeltaCoreError(
                f"delta_core handshake rejected: {resp.get('error', 'unknown error')}"
            )
        server_version = (resp.get("result") or {}).get("protocol_version")
        if server_version != PROTOCOL_VERSION:
            self.close()
            raise DeltaCoreError(
                f"delta_core protocol version mismatch: "
                f"client={PROTOCOL_VERSION}, server={server_version}"
            )

    # -- stdout reader (demux) ---------------------------------------------------

    def _stdout_reader(self, proc: subprocess.Popen) -> threading.Thread:
        """Start a background thread that reads stdout and routes by request_id.

        Each line is parsed as JSON, and the ``request_id`` field determines
        which queue the frame goes to:

        * For non-streaming responses (no ``stream`` key): routed to
          ``_pending[request_id]``.
        * For streaming frames (has ``stream`` key): routed to
          ``_streams[request_id]``.

        On EOF or error, crashes all pending requests and streams.
        """

        def reader():
            try:
                stdout = proc.stdout
                if stdout is None:
                    return
                while not self._reader_stop.is_set():
                    line = stdout.readline()
                    if not line:
                        # EOF — process died or closed stdout. If this was
                        # a deliberate close(), _reader_stop is set and we
                        # must not crash-fanout (a restart may already be
                        # registering new pending entries).
                        if not self._reader_stop.is_set():
                            self._crash(DeltaCoreError("delta_core closed stdout"))
                        return
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        frame = json.loads(line)
                    except json.JSONDecodeError:
                        # Non-JSON line — ignore (may be a Rust panic message)
                        continue
                    request_id = frame.get("request_id")
                    if request_id is None:
                        request_id = 0
                    elif isinstance(request_id, str):
                        try:
                            request_id = int(request_id)
                        except ValueError:
                            request_id = 0
                    stream_kind = frame.get("stream")
                    with self._pending_lock:
                        if stream_kind is not None:
                            q = self._streams.get(request_id)
                        else:
                            q = self._pending.get(request_id)
                    if q is not None:
                        q.put(frame)
            except Exception as exc:
                if not self._reader_stop.is_set():
                    self._crash(DeltaCoreError(f"delta_core reader error: {exc}"))
            finally:
                if not self._reader_stop.is_set():
                    self._crash(DeltaCoreError("delta_core reader thread exited"))

        thread = threading.Thread(
            target=reader, name="delta-core-stdout-reader", daemon=True
        )
        thread.start()
        return thread

    def _crash(self, error: Exception) -> None:
        """Fan out a crash error to all pending requests and streams.
        Also releases all inflight slots since no request can complete."""
        with self._pending_lock:
            for q in self._pending.values():
                q.put({"ok": False, "error": str(error)})
            for q in self._streams.values():
                q.put({"ok": False, "stream": "error", "error": str(error)})
            self._pending.clear()
            self._streams.clear()
        # Release all inflight slots — crashed requests can never complete.
        with self._running_lock:
            n = len(self._running)
            self._running.clear()
        for _ in range(n):
            try:
                self._inflight.release()
            except ValueError:
                break  # semaphore underflow guard

    def close(self) -> None:
        """Close the subprocess. Subsequent commands will restart it."""
        self._crash(DeltaCoreError("delta_core client closed"))
        with self._proc_lock:
            proc = self._proc
            self._proc = None
            self._reader_stop.set()
            self._stderr_stop.set()
            if proc is not None:
                try:
                    if proc.stdin is not None:
                        try:
                            proc.stdin.close()
                        except Exception:
                            pass
                    try:
                        proc.wait(timeout=2)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        try:
                            proc.wait(timeout=2)
                        except Exception:
                            pass
                finally:
                    for thread in (self._reader_thread, self._stderr_thread):
                        if thread is not None and thread.is_alive():
                            thread.join(timeout=1.0)
                    self._reader_thread = None
                    self._stderr_thread = None

    # -- public API ---------------------------------------------------------------

    def _acquire_inflight(self) -> None:
        """AF-07: admission control. Acquire one inflight slot.

        If the bounded queue is full, raise DeltaCoreError("overload")
        rather than queuing indefinitely. This prevents unbounded memory
        growth and ensures approval/ledger requests are never starved.

        Control commands (request.cancel, ping) bypass the limit so
        cancellation and health checks always succeed even at full load.
        """
        if not self._inflight.acquire(timeout=0.1):
            raise DeltaCoreError(
                "delta_core overload: too many concurrent in-flight requests"
            )

    def _release_inflight(self) -> None:
        self._inflight.release()

    def command(self, payload: dict[str, Any]) -> Any:
        """Send one command, wait for its response, return the ``result``.

        AF-07: bounded inflight — raises ``DeltaCoreError("overload")`` if
        the max concurrent request limit is exceeded. Control commands
        (request.cancel, ping, hello) bypass the limit.
        """
        # Control commands bypass backpressure — they must always succeed
        # even when the client is at full inflight capacity.
        is_control = payload.get("cmd") in (
            "request.cancel", "ping", "hello",
        )
        if not is_control:
            self._acquire_inflight()
        request_id = self._alloc_id()
        payload = {**payload, "request_id": request_id}

        # AF-12: register RunningTask — the caller (this thread) owns
        # completion. On close/crash, the registry is fanned out.
        with self._running_lock:
            self._running[request_id] = {
                "started_at": time.time(),
                "owner": threading.current_thread().ident,
                "cmd": payload.get("cmd"),
            }

        # Register the pending queue before sending the request.
        q: queue.Queue = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[request_id] = q

        try:
            self._ensure_started()
            proc = self._proc
            if proc is None or proc.stdin is None:
                self.close()
                raise DeltaCoreError("delta_core process pipes unavailable")
            line = json.dumps(payload)
            try:
                with self._write_lock:
                    proc.stdin.write(line + "\n")
                    proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                self.close()
                raise DeltaCoreError(f"delta_core stdin write failed: {exc}") from exc

            # Wait for the response (with timeout).
            try:
                response = q.get(timeout=self._command_timeout)
            except queue.Empty:
                self.close()
                raise DeltaCoreError(
                    f"delta_core command {payload.get('cmd')} timed out after {self._command_timeout}s"
                )

            if not response.get("ok"):
                raise DeltaCoreError(
                    f"delta_core {payload.get('cmd')}: {response.get('error')}"
                )
            return response.get("result")
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            was_running = False
            with self._running_lock:
                was_running = request_id in self._running
                self._running.pop(request_id, None)
            if is_control:
                pass
            elif was_running:
                self._release_inflight()

    def stream(
        self, payload: dict[str, Any]
    ) -> "Generator[dict[str, Any], None, Any]":
        """Send a streaming command, yielding delta frames as they arrive.

        The caller must consume the generator fully (or close it) to release
        the stream resources. Other commands can be issued concurrently —
        the reader thread routes frames by ``request_id``.

        AF-07: bounded inflight — raises ``DeltaCoreError("overload")`` if
        the max concurrent request limit is exceeded.
        """
        self._acquire_inflight()
        request_id = self._alloc_id()
        payload = {**payload, "request_id": request_id}

        # AF-12: register RunningTask.
        with self._running_lock:
            self._running[request_id] = {
                "started_at": time.time(),
                "owner": threading.current_thread().ident,
                "cmd": payload.get("cmd"),
            }

        # Register the stream queue before sending the request.
        q: queue.Queue = queue.Queue()  # unbounded for deltas
        with self._pending_lock:
            self._streams[request_id] = q

        self._ensure_started()
        proc = self._proc
        if proc is None or proc.stdin is None:
            self.close()
            raise DeltaCoreError("delta_core process pipes unavailable")
        line = json.dumps(payload)
        try:
            with self._write_lock:
                proc.stdin.write(line + "\n")
                proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            self.close()
            raise DeltaCoreError(f"delta_core stdin write failed: {exc}") from exc

        # Return a generator that reads from the stream queue.
        def _gen() -> Generator[dict[str, Any], None, Any]:
            try:
                while True:
                    frame = q.get(timeout=self._command_timeout)
                    if not frame.get("ok"):
                        raise DeltaCoreError(
                            f"delta_core stream error: {frame.get('error')}"
                        )
                    stream_kind = frame.get("stream")
                    if stream_kind == "start":
                        continue
                    if stream_kind == "delta":
                        yield frame.get("data")
                        continue
                    if stream_kind == "done":
                        return frame.get("result")
                    if stream_kind == "error":
                        raise DeltaCoreError(
                            f"delta_core stream error: {frame.get('error')}"
                        )
                    raise DeltaCoreError(
                        f"delta_core stream: unknown frame type {stream_kind!r}"
                    )
            finally:
                was_running = False
                with self._running_lock:
                    was_running = request_id in self._running
                    self._running.pop(request_id, None)
                with self._pending_lock:
                    self._streams.pop(request_id, None)
                # Only release if we were still registered — _crash()
                # may have already released the slot for us.
                if was_running:
                    self._release_inflight()

        return _gen()

    def stream_cancel(self, request_id: int) -> Any:
        """Cancel an in-flight stream by ``request_id``.

        Sends ``request.cancel`` to the server, which sets the cancel flag
        for the target stream. The stream thread exits at the next delta
        boundary. Other requests are unaffected.
        """
        return self.command({
            "cmd": "request.cancel",
            "target_request_id": request_id,
        })

    @property
    def active_inflight(self) -> int:
        """AF-12: number of currently registered in-flight requests."""
        with self._running_lock:
            return len(self._running)

    def shutdown(self, *, graceful_timeout: float = 5.0) -> None:
        """AF-13: graceful shutdown with bounded deadline.

        1. Signal cancel for all active streams (graceful).
        2. Inject error frames into stream queues so generators exit.
        3. Wait up to ``graceful_timeout`` for inflight to drain.
        4. If still not drained, force-close (abort).
        """
        with self._running_lock:
            ids = list(self._running.keys())
        # Send cancel to Rust for each active stream.
        for rid in ids:
            try:
                self.stream_cancel(rid)
            except Exception:
                pass
        # Inject error frames into stream queues so generators that are
        # blocked on q.get() will exit and release inflight slots.
        with self._pending_lock:
            for q in self._streams.values():
                q.put({"ok": False, "stream": "error", "error": "shutdown"})
        # Wait for inflight to drain.
        deadline = time.time() + graceful_timeout
        while time.time() < deadline:
            if self.active_inflight == 0:
                break
            time.sleep(0.05)
        self.close()


_default_client: DeltaCoreClient | None = None
_default_lock = threading.Lock()


def default_client() -> DeltaCoreClient:
    """Return the process-wide shared delta_core client."""
    global _default_client
    with _default_lock:
        if _default_client is None:
            _default_client = DeltaCoreClient()
        return _default_client


def close_default_client() -> None:
    """Close the process-wide shared client. Safe to call multiple times."""
    global _default_client
    with _default_lock:
        if _default_client is not None:
            _default_client.close()
            _default_client = None


def maybe_core_client() -> DeltaCoreClient | None:
    """Return the shared delta_core client if a binary is available, else None."""
    if _find_delta_core_binary() is None:
        return None
    return default_client()
