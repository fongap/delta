"""Managed configuration model.

Replaces the old 5 cloud_* config fields with a single nested struct.
Default = disabled, empty, no network call.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ManagedConfig:
    """Minimal managed-service configuration.

    Default: disabled, empty URLs, empty token — a fresh install makes NO
    network calls to any managed service. A future Delta Hub deployment
    fills these in; until then, all managed brokers are Null*.
    """

    enabled: bool = False
    base_url: str = ""
    device_token: str = ""
    relay_ws_url: str = ""
