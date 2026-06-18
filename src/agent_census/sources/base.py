"""Source protocol + shared result assembly."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from .. import __version__
from ..errors import DiscoveryError
from ..findings import DEFAULT_STALE_DAYS, annotate, build_summary
from ..models import Agent, SweepMeta, SweepResult

__all__ = ["DiscoveryError", "Source", "build_result"]


@runtime_checkable
class Source(Protocol):
    """A discovery source produces a :class:`SweepResult`."""

    name: str

    def scan(self) -> SweepResult: ...


def build_result(
    agents: Iterable[Agent],
    *,
    source: str,
    environment: str | None = None,
    warnings: Iterable[str] | None = None,
    now: datetime | None = None,
    stale_days: int = DEFAULT_STALE_DAYS,
) -> SweepResult:
    """Annotate agents with findings/category, roll up a summary, and wrap in a result."""
    now = now or datetime.now(UTC)
    agent_list = list(agents)
    for agent in agent_list:
        annotate(agent, now=now, stale_days=stale_days)
    return SweepResult(
        meta=SweepMeta(
            generated_at=now,
            source=source,
            tool_version=__version__,
            environment=environment,
        ),
        summary=build_summary(agent_list),
        agents=agent_list,
        warnings=list(warnings or []),
    )
