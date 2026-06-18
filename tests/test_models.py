from datetime import UTC, datetime

from agent_census.models import Agent, Summary, SweepMeta, SweepResult


def test_agent_roundtrip_and_model_field():
    a = Agent(
        name="x",
        external_id="id1",
        source_system="copilot_studio",
        model="GPT-4o",  # field literally named `model` — needs protected_namespaces=()
        instructions="hello",
    )
    data = a.model_dump(mode="json")
    a2 = Agent.model_validate(data)
    assert a2 == a
    assert a2.model == "GPT-4o"
    assert a2.source_system.value == "copilot_studio"
    assert a2.instructions_length == len("hello")


def test_instructions_length_always_derived():
    a = Agent(
        name="x",
        external_id="i",
        source_system="azure_ai_foundry",
        instructions="abc",
        instructions_length=999,  # ignored — always recomputed
    )
    assert a.instructions_length == 3


def test_sweepresult_roundtrip():
    meta = SweepMeta(
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        source="demo",
        tool_version="0.1.0",
    )
    r = SweepResult(meta=meta, summary=Summary(), agents=[])
    r2 = SweepResult.model_validate(r.model_dump(mode="json"))
    assert r2.schema_version == "agentcensus/v1"
    assert r2.meta.source == "demo"


def test_max_severity_rank():
    clean = Agent(name="a", external_id="i", source_system="copilot_studio")
    assert clean.max_severity_rank == -1
