"""Demo source — a bundled synthetic estate so anyone can see a full report.

Zero setup, zero network. The records in ``fixtures/demo_estate.json`` are
already in :class:`~agent_census.models.Agent` shape, so they validate directly
(no normalization). Findings and the summary are computed on the fly, so the
report always reflects the current rule set.
"""

from __future__ import annotations

import json
from importlib.resources import files

from ..findings import DEFAULT_STALE_DAYS
from ..models import Agent, SweepResult
from .base import DiscoveryError, build_result


class DemoSource:
    name = "demo"

    def __init__(self, *, stale_days: int = DEFAULT_STALE_DAYS) -> None:
        self.stale_days = stale_days

    def scan(self) -> SweepResult:
        try:
            raw = (files("agent_census.fixtures") / "demo_estate.json").read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover - packaging error
            raise DiscoveryError(f"could not load demo data: {exc}") from exc

        agents = [Agent.model_validate(item) for item in data.get("agents", [])]
        return build_result(agents, source="demo", stale_days=self.stale_days)
