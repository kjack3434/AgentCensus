"""Run several live connectors and merge them into one unified inventory."""

from __future__ import annotations

from collections.abc import Sequence

from ..errors import DiscoveryError
from ..findings import DEFAULT_STALE_DAYS
from ..models import SweepResult
from .base import Source, build_result


class AllSource:
    name = "all"

    def __init__(self, sources: Sequence[Source], *, stale_days: int = DEFAULT_STALE_DAYS) -> None:
        self._sources = list(sources)
        self.stale_days = stale_days

    def scan(self) -> SweepResult:
        agents = []
        warnings: list[str] = []
        ran: list[str] = []

        for source in self._sources:
            try:
                result = source.scan()
            except DiscoveryError as exc:
                warnings.append(f"{source.name}: {exc}")
                continue
            agents.extend(result.agents)
            warnings.extend(result.warnings)
            ran.append(source.name)

        if not ran:
            raise DiscoveryError(
                "no live connector succeeded — check credentials/permissions "
                "(see warnings) or run with --demo"
            )

        # Re-derive findings + summary across the merged set for one unified view.
        return build_result(
            agents,
            source="all",
            environment=", ".join(ran),
            warnings=warnings,
            stale_days=self.stale_days,
        )
