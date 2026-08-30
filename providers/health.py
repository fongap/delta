"""Per-endpoint/model health profile (v0.3.0 P2): success rate + latency per
gateway/model pair, persisted and fed back into routing.

The request log (`core/request_log.py`) records one row per model call, but it is
keyed by provider class name — not by ENDPOINT. A degraded gateway is only visible
once its base_url is aggregated, so this store keeps its own keyed-by-(endpoint,
model) rolling window: how many calls, how many succeeded, average TTFT / duration,
last error class. It is written from the provider layer (which knows the endpoint)
and read when building providers / surfacing errors, so a flaky gateway can:

- warn in the error payload ("this gateway has failed N of the last M calls"),
- feed routing decisions (pick the healthiest configured endpoint for a model),
- be inspected from Settings without grepping request logs.

Persistence is best-effort JSON in the state dir (like endpoint_caps.json); a write
failure must never break a model call.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Rolling window: keep at most this many observations per (endpoint, model) key, and
# drop entries older than this many seconds — a window is "recent behaviour", not
# lifetime average.
_MAX_SAMPLES = 200
_MAX_AGE_SECONDS = 24 * 3600

_lock = threading.Lock()


@dataclass
class HealthProfile:
    """Aggregated health for one (endpoint, model) pair over its recent window."""

    endpoint: str
    model: str
    samples: int = 0
    errors: int = 0
    ttft_ms: list[float] = field(default_factory=list)
    duration_ms: list[float] = field(default_factory=list)
    last_error_class: str | None = None
    last_ts: float = 0.0

    @property
    def success_rate(self) -> float:
        if not self.samples:
            return 1.0
        return (self.samples - self.errors) / self.samples

    @property
    def avg_ttft_ms(self) -> float:
        return sum(self.ttft_ms) / len(self.ttft_ms) if self.ttft_ms else 0.0

    @property
    def avg_duration_ms(self) -> float:
        return sum(self.duration_ms) / len(self.duration_ms) if self.duration_ms else 0.0

    @property
    def degraded(self) -> bool:
        """A gateway/model is degraded when it has enough samples to judge and is
        failing more than a quarter of recent calls."""
        return self.samples >= 5 and self.success_rate < 0.75

    def as_dict(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "model": self.model,
            "samples": self.samples,
            "errors": self.errors,
            "success_rate": round(self.success_rate, 3),
            "avg_ttft_ms": round(self.avg_ttft_ms, 1),
            "avg_duration_ms": round(self.avg_duration_ms, 1),
            "last_error_class": self.last_error_class,
            "last_ts": round(self.last_ts, 1),
            "degraded": self.degraded,
        }


def _store_path() -> Path:
    from packages.secrets import state_dir

    return state_dir() / "provider_health.json"


def _read_store() -> dict[str, dict[str, dict[str, Any]]]:
    try:
        return json.loads(_store_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_store(store: dict[str, dict[str, dict[str, Any]]]) -> None:
    try:
        path = _store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(store), encoding="utf-8")
    except Exception:
        pass  # health is best-effort; never break a call


def record_call(
    endpoint: str | None,
    model: str,
    *,
    ok: bool,
    ttft_ms: float | None = None,
    duration_ms: float | None = None,
    error_class: str | None = None,
) -> None:
    """Record one model call outcome for (endpoint, model). `endpoint` is the base_url
    (or provider name when no endpoint applies); None keys are ignored."""
    if not endpoint or not model:
        return
    with _lock:
        store = _read_store()
        bucket = store.setdefault(endpoint, {})
        row = bucket.setdefault(
            model,
            {
                "samples": 0,
                "errors": 0,
                "ttft_ms": [],
                "duration_ms": [],
                "last_error_class": None,
                "last_ts": 0.0,
            },
        )
        row["samples"] = int(row.get("samples", 0)) + 1
        if not ok:
            row["errors"] = int(row.get("errors", 0)) + 1
            row["last_error_class"] = error_class
        # Cap the rolling buffers and drop stale entries.
        now = time.time()
        ttft = list(row.get("ttft_ms") or [])
        dur = list(row.get("duration_ms") or [])
        if ttft_ms is not None:
            ttft.append(float(ttft_ms))
        if duration_ms is not None:
            dur.append(float(duration_ms))
        row["ttft_ms"] = ttft[-_MAX_SAMPLES:]
        row["duration_ms"] = dur[-_MAX_SAMPLES:]
        row["last_ts"] = now
        # Drop other (endpoint, model) rows for this endpoint that went stale — the
        # window is recent behaviour.
        for m, other in list(bucket.items()):
            if m != model and now - float(other.get("last_ts", 0)) > _MAX_AGE_SECONDS:
                bucket.pop(m, None)
        _write_store(store)


def profile(endpoint: str | None, model: str) -> HealthProfile:
    """The aggregated health profile for (endpoint, model), or an empty one."""
    if not endpoint or not model:
        return HealthProfile(endpoint=endpoint or "", model=model)
    with _lock:
        store = _read_store()
        row = (store.get(endpoint) or {}).get(model) or {}
    return HealthProfile(
        endpoint=endpoint,
        model=model,
        samples=int(row.get("samples", 0)),
        errors=int(row.get("errors", 0)),
        ttft_ms=[float(x) for x in (row.get("ttft_ms") or [])],
        duration_ms=[float(x) for x in (row.get("duration_ms") or [])],
        last_error_class=row.get("last_error_class"),
        last_ts=float(row.get("last_ts", 0.0)),
    )


def all_profiles() -> list[HealthProfile]:
    """Every (endpoint, model) profile — for Settings / diagnostics."""
    out: list[HealthProfile] = []
    with _lock:
        store = _read_store()
    for endpoint, bucket in store.items():
        for model, row in bucket.items():
            out.append(
                HealthProfile(
                    endpoint=endpoint,
                    model=model,
                    samples=int(row.get("samples", 0)),
                    errors=int(row.get("errors", 0)),
                    ttft_ms=[float(x) for x in (row.get("ttft_ms") or [])],
                    duration_ms=[float(x) for x in (row.get("duration_ms") or [])],
                    last_error_class=row.get("last_error_class"),
                    last_ts=float(row.get("last_ts", 0.0)),
                )
            )
    return out


def degraded(endpoint: str | None, model: str) -> bool:
    return profile(endpoint, model).degraded


def route_healthy(
    candidates: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Order (endpoint, model) candidates best-first by health for routing: healthy
    endpoints first, unknowns in the middle, degraded last. Stable for ties."""
    def _key(candidate: tuple[str, str]) -> tuple[int, float]:
        ep, model = candidate
        p = profile(ep, model)
        if p.samples == 0:
            return (1, 0.0)  # unknown → middle
        if p.degraded:
            return (2, p.success_rate)
        return (0, -p.success_rate)  # healthy → front

    return sorted(candidates, key=_key)
