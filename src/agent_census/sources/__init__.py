"""Discovery sources and the registry that resolves ``--source`` to one."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from ..findings import DEFAULT_STALE_DAYS
from .base import DiscoveryError, Source, build_result

if TYPE_CHECKING:
    from ..live.auth import TokenProvider

__all__ = ["DiscoveryError", "Source", "build_result", "build_source"]

# Which provider's auth each live connector needs.
MICROSOFT_SOURCES = ("copilot_studio", "foundry")
GCP_SOURCES = ("gcp",)


def build_source(
    sources: Sequence[str],
    *,
    stale_days: int = DEFAULT_STALE_DAYS,
    ms_auth: TokenProvider | None = None,
    gcp_auth: TokenProvider | None = None,
    environment: str | None = None,
    subscription: str | None = None,
    projects: list[str] | None = None,
    locations: list[str] | None = None,
) -> Source:
    """Build a :class:`Source` from one or more connector keys.

    Each connector gets its provider's token provider (``ms_auth`` for Copilot
    Studio / Foundry, ``gcp_auth`` for GCP). One key → that source; several →
    an :class:`AllSource` that merges them and demotes per-connector failures to
    warnings. ``["demo"]`` is the offline synthetic estate. Imports are lazy so
    the demo path never pulls in the live HTTP stack.
    """
    keys = list(sources)
    if keys == ["demo"]:
        from .demo import DemoSource

        return DemoSource(stale_days=stale_days)

    built: list[Source] = []
    for key in keys:
        if key == "copilot_studio":
            from .copilot_studio import CopilotStudioLiveSource

            built.append(
                CopilotStudioLiveSource(ms_auth, environment=environment, stale_days=stale_days)
            )
        elif key == "foundry":
            from .foundry import FoundryLiveSource

            built.append(
                FoundryLiveSource(ms_auth, subscription=subscription, stale_days=stale_days)
            )
        elif key == "gcp":
            from .gcp import GcpLiveSource

            built.append(
                GcpLiveSource(
                    gcp_auth, projects=projects, locations=locations, stale_days=stale_days
                )
            )
        else:
            raise DiscoveryError(f"unknown source: {key!r}")

    if not built:
        raise DiscoveryError("no sources selected")
    if len(built) == 1:
        return built[0]

    from .all import AllSource

    return AllSource(built, stale_days=stale_days)
