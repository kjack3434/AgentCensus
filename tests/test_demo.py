from agent_census.sources import build_source

_GCP_SOURCES = {"vertex_ai_agent_engine", "dialogflow_cx", "agentspace"}


def test_demo_source_loads_estate():
    r = build_source(["demo"]).scan()
    assert r.meta.source == "demo"
    assert r.summary.total_agents == 16
    assert set(r.summary.by_source) == {"copilot_studio", "azure_ai_foundry"} | _GCP_SOURCES
    # at least one CRITICAL so --fail-on tests are meaningful
    assert r.summary.findings_by_severity.get("critical", 0) >= 1
    # all three ecosystems are represented
    sources = {a.source_system.value for a in r.agents}
    assert sources == {"copilot_studio", "azure_ai_foundry"} | _GCP_SOURCES
    # every agent has a computed category
    assert all(a.category for a in r.agents)


def test_demo_gcp_findings_are_suppressed_where_unobservable():
    r = build_source(["demo"]).scan()
    by_id = {a.external_id: a for a in r.agents}
    # Code-deployed Agent Engine agent: model + instructions unobservable -> no 007/009.
    engine = by_id["projects/contoso-demo/locations/us-central1/reasoningEngines/4815162342"]
    rules = {f.rule_id for f in engine.findings}
    assert "SWEEP-007" not in rules and "SWEEP-009" not in rules
    # Dialogflow exposes a write-capable tool with no approval gate -> 005 fires,
    # but its model is still unobservable -> 007 suppressed.
    bot = next(a for a in r.agents if a.source_system.value == "dialogflow_cx")
    bot_rules = {f.rule_id for f in bot.findings}
    assert "SWEEP-005" in bot_rules
    assert "SWEEP-007" not in bot_rules
