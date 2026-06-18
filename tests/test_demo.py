from agent_census.sources import build_source


def test_demo_source_loads_estate():
    r = build_source("demo").scan()
    assert r.meta.source == "demo"
    assert r.summary.total_agents == 13
    assert set(r.summary.by_source) == {"copilot_studio", "azure_ai_foundry"}
    # at least one CRITICAL so --fail-on tests are meaningful
    assert r.summary.findings_by_severity.get("critical", 0) >= 1
    # both ecosystems are represented
    sources = {a.source_system.value for a in r.agents}
    assert sources == {"copilot_studio", "azure_ai_foundry"}
    # every agent has a computed category
    assert all(a.category for a in r.agents)
