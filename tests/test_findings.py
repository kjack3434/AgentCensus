from datetime import UTC, datetime, timedelta

from agent_census.findings import build_summary, categorize, evaluate
from agent_census.models import (
    Agent,
    GuardrailRef,
    KnowledgeKind,
    KnowledgeRef,
    OwnerRef,
    ToolRef,
    ToolType,
)

NOW = datetime(2026, 6, 18, tzinfo=UTC)


def mk(**kw) -> Agent:
    base = dict(
        name="a",
        external_id="i",
        source_system="copilot_studio",
        owners=[OwnerRef(name="Owner")],
        guardrails=[GuardrailRef(kind="content_safety")],
        model="GPT-4o",
        model_tier="standard",
        instructions="do useful things",
        modified_on=NOW,
    )
    base.update(kw)
    return Agent(**base)


def codes(a: Agent) -> set[str]:
    return {f.rule_id for f in evaluate(a, now=NOW)}


def test_clean_agent_has_no_findings():
    assert codes(mk()) == set()


def test_public_unauthenticated():
    assert "SWEEP-001" in codes(mk(no_auth_required=True, shared_with_everyone=True))
    assert "SWEEP-001" not in codes(mk(no_auth_required=True))  # not shared


def test_missing_owner_and_no_guardrails_are_not_findings():
    # absence of owner / guardrails is never scored (not discoverable / on by default)
    assert codes(mk(owners=[], guardrails=[])) == set()


def test_autonomous_without_human():
    assert "SWEEP-004" in codes(mk(autonomous=True))
    assert "SWEEP-004" not in codes(mk(autonomous=True, properties={"has_human_in_the_loop": True}))


def test_write_tool_without_approval():
    risky = ToolRef(
        name="CreateThing", tool_type=ToolType.HTTP, write_capable=True, requires_approval="never"
    )
    assert "SWEEP-005" in codes(mk(tools=[risky]))
    safe = ToolRef(
        name="Lookup", tool_type=ToolType.FUNCTION, write_capable=False, requires_approval="always"
    )
    assert "SWEEP-005" not in codes(mk(tools=[safe]))


def test_external_mcp():
    assert "SWEEP-006" in codes(mk(tools=[ToolRef(name="X", tool_type=ToolType.MCP)]))


def test_ungoverned_model():
    assert "SWEEP-007" in codes(mk(model_tier="preview"))
    assert "SWEEP-007" in codes(mk(model="unknown", model_tier=None))
    assert "SWEEP-007" in codes(mk(model=None, model_tier=None))
    assert "SWEEP-007" not in codes(mk())


def test_broad_channels():
    assert "SWEEP-008" in codes(mk(channels=["Microsoft Teams", "Slack", "Web Chat"]))
    assert "SWEEP-008" in codes(mk(shared_with_everyone=True, channels=["Slack"]))
    assert "SWEEP-008" not in codes(mk(channels=["Microsoft Teams"]))


def test_broadly_shared_capable():
    write = ToolRef(name="CreateThing", tool_type=ToolType.HTTP, write_capable=True)
    # shared + a capability amplifier -> fires
    assert "SWEEP-011" in codes(mk(shared_with_everyone=True, tools=[write]))
    assert "SWEEP-011" in codes(mk(multi_tenant=True, autonomous=True))
    assert "SWEEP-011" in codes(mk(shared_with_everyone=True, channels=["Slack"]))
    # shared but read-only / no amplifier -> NOT flagged (intended, common)
    assert "SWEEP-011" not in codes(mk(shared_with_everyone=True))
    # capable but not shared -> NOT flagged
    assert "SWEEP-011" not in codes(mk(tools=[write]))


def test_external_data_connection():
    ext = KnowledgeRef(
        name="SharePoint HR",
        kind=KnowledgeKind.DISCOVERY_ENGINE_DATASTORE,
        external_source="sharepoint",
    )
    native = KnowledgeRef(name="Drive", kind=KnowledgeKind.DISCOVERY_ENGINE_DATASTORE)
    assert "SWEEP-012" in codes(mk(knowledge=[ext]))
    assert "SWEEP-012" not in codes(mk(knowledge=[native]))


def test_empty_instructions():
    assert "SWEEP-009" in codes(mk(instructions=""))
    assert "SWEEP-009" in codes(mk(instructions="You are a helpful assistant."))


def test_stale():
    assert "SWEEP-010" in codes(mk(modified_on=NOW - timedelta(days=200)))
    assert "SWEEP-010" not in codes(mk(modified_on=NOW - timedelta(days=10)))


def test_findings_sorted_by_severity():
    a = mk(
        no_auth_required=True,
        shared_with_everyone=True,
        autonomous=True,
        channels=["Microsoft Teams", "Slack", "Web Chat"],
        instructions="",
    )
    found = evaluate(a, now=NOW)
    severities = [f.severity.value for f in found]
    # critical first, info/low last
    assert severities[0] == "critical"
    assert severities == sorted(severities, key=["critical", "high", "medium", "low", "info"].index)


def test_categorize():
    assert categorize(mk(autonomous=True)) == "autonomous"
    # shared org-wide internally -> org_wide, NOT customer_facing
    assert categorize(mk(shared_with_everyone=True)) == "org_wide"
    # genuinely external reach -> customer_facing
    assert categorize(mk(multi_tenant=True)) == "customer_facing"
    assert categorize(mk(channels=["Slack"])) == "customer_facing"
    # external reach wins over org-wide sharing
    assert categorize(mk(shared_with_everyone=True, channels=["Slack"])) == "customer_facing"
    assert categorize(mk(owners=[])) == "internal"  # missing owner no longer = orphaned
    assert categorize(mk()) == "internal"


def test_build_summary():
    agents = [mk(), mk(no_auth_required=True, shared_with_everyone=True, guardrails=[])]
    for a in agents:
        a.findings = evaluate(a, now=NOW)
        a.category = categorize(a)
    s = build_summary(agents)
    assert s.total_agents == 2
    assert s.by_source["copilot_studio"] == 2
    assert s.findings_by_severity.get("critical", 0) >= 1
    assert s.total_findings == sum(s.findings_by_severity.values())
