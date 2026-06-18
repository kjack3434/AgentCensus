"""Discovery sources and the registry that resolves ``--source`` to one."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..findings import DEFAULT_STALE_DAYS
from .base import DiscoveryError, Source, build_result

if TYPE_CHECKING:
    from ..live.auth import TokenProvider

__all__ = ["DiscoveryError", "Source", "build_result", "build_source"]


def build_source(
    source: str,
    *,
    stale_days: int = DEFAULT_STALE_DAYS,
    auth: TokenProvider | None = None,
    environment: str | None = None,
    subscription: str | None = None,
) -> Source:
    """Resolve a ``--source`` value to a constructed :class:`Source`.

    Live sources require an ``auth`` token provider; ``demo`` does not. Imports
    are lazy so the demo path never pulls in the live HTTP stack.
    """
    key = source.strip().lower().replace("-", "_")

    if key == "demo":
        from .demo import DemoSource

        return DemoSource(stale_days=stale_days)

    if key == "copilot_studio":
        from .copilot_studio import CopilotStudioLiveSource

        return CopilotStudioLiveSource(auth, environment=environment, stale_days=stale_days)

    if key == "foundry":
        from .foundry import FoundryLiveSource

        return FoundryLiveSource(auth, subscription=subscription, stale_days=stale_days)

    if key == "all":
        from .all import AllSource
        from .copilot_studio import CopilotStudioLiveSource
        from .foundry import FoundryLiveSource

        return AllSource(
            [
                CopilotStudioLiveSource(auth, environment=environment, stale_days=stale_days),
                FoundryLiveSource(auth, subscription=subscription, stale_days=stale_days),
            ],
            stale_days=stale_days,
        )

    raise DiscoveryError(f"unknown source: {source!r}")
