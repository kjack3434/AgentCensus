"""AllSource merge + graceful-skip behavior."""

import pytest

from agent_census.errors import DiscoveryError
from agent_census.models import Agent
from agent_census.sources.all import AllSource
from agent_census.sources.base import build_result


class FakeSource:
    def __init__(self, name, count):
        self.name = name
        self._count = count

    def scan(self):
        agents = [
            Agent(
                name=f"{self.name}-{i}",
                external_id=f"{self.name}:{i}",
                source_system="copilot_studio",
            )
            for i in range(self._count)
        ]
        return build_result(agents, source=self.name)


class BoomSource:
    name = "boom"

    def scan(self):
        raise DiscoveryError("no credentials")


def test_all_source_merges():
    result = AllSource([FakeSource("a", 2), FakeSource("b", 1)]).scan()
    assert result.meta.source == "all"
    assert result.summary.total_agents == 3
    assert result.meta.environment == "a, b"


def test_all_source_skips_failed_connector():
    result = AllSource([FakeSource("a", 2), BoomSource()]).scan()
    assert result.summary.total_agents == 2
    assert any("boom" in w for w in result.warnings)
    assert result.meta.environment == "a"


def test_all_source_raises_when_all_fail():
    with pytest.raises(DiscoveryError):
        AllSource([BoomSource()]).scan()
