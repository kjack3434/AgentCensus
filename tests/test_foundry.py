"""Foundry normalization + live source (mocked, no network)."""

from agent_census.live.foundry import derive_project_endpoint
from agent_census.models import AgentKind, KnowledgeKind, SourceSystem, ToolType
from agent_census.normalize import foundry_def_to_agent


def test_derive_endpoint_rejects_spoofed_host():
    # custom (non-standard) key forces the host-validated lookup loop
    acct = {
        "name": "acc",
        "properties": {
            "endpoints": {"customFoundry": "https://services.ai.azure.com.evil.example"}
        },
    }
    # spoofed host is rejected → falls back to the constructed account host
    assert (
        derive_project_endpoint(acct, "p1") == "https://acc.services.ai.azure.com/api/projects/p1"
    )


def test_derive_endpoint_accepts_real_subdomain():
    acct = {
        "name": "acc",
        "properties": {"endpoints": {"customFoundry": "https://acc.services.ai.azure.com"}},
    }
    assert (
        derive_project_endpoint(acct, "p1") == "https://acc.services.ai.azure.com/api/projects/p1"
    )


class FakeAuth:
    def get_token(self, scope: str) -> str:
        return "fake-token"


def test_foundry_v2_nested_shape():
    raw = {
        "id": "asst_1",
        "name": "Claims",
        "versions": {
            "latest": {
                "description": "d",
                "status": "active",
                "version": "3",
                "definition": {
                    "kind": "prompt",
                    "model": "gpt-4o",
                    "instructions": "help",
                    "tools": [
                        {
                            "type": "mcp",
                            "server_label": "DevMCP",
                            "server_url": "https://m",
                            "require_approval": "never",
                        },
                        {"type": "azure_ai_search", "index_name": "idx"},
                        {"type": "code_interpreter"},
                        {"type": "file_search"},
                    ],
                    "rai_config": {"contentFilter": "high"},
                },
            }
        },
    }
    a = foundry_def_to_agent(raw, project_external_id="proj")
    assert a.source_system is SourceSystem.AZURE_AI_FOUNDRY
    assert a.external_id == "proj:agent:asst_1"
    assert a.kind is AgentKind.AGENT  # Foundry "prompt"/"hosted" surface as "agent"
    assert a.model == "gpt-4o"
    assert a.version == "3"
    tool_types = {t.tool_type for t in a.tools}
    assert ToolType.MCP in tool_types
    assert ToolType.CODE_INTERPRETER in tool_types
    # grounding tools become knowledge, not tools
    kinds = {k.kind for k in a.knowledge}
    assert KnowledgeKind.AZURE_SEARCH in kinds
    assert KnowledgeKind.FILE_UPLOAD in kinds
    assert a.guardrails and a.guardrails[0].kind == "content_safety"
    # the MCP tool is not write-capable but the code_interpreter is
    assert any(t.tool_type is ToolType.CODE_INTERPRETER and t.write_capable for t in a.tools)


def test_foundry_v1_flat_and_conditional_approval():
    raw = {
        "id": "a2",
        "name": "Flat",
        "kind": "workflow",
        "model": "gpt-4o-preview",
        "instructions": "",
        "tools": [{"type": "mcp", "require_approval": {"never": {}, "always": {}}}],
    }
    a = foundry_def_to_agent(raw)
    assert a.kind is AgentKind.WORKFLOW
    assert a.model_tier == "preview"
    assert a.tools[0].requires_approval == "conditional"


def test_foundry_source_assembles_result(monkeypatch):
    import agent_census.live.foundry as fdy

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def list_subscriptions(self):
            return ["sub1"]

        def list_ai_accounts(self, sub):
            return [
                {
                    "id": "/subscriptions/sub1/resourceGroups/rg/providers/"
                    "Microsoft.CognitiveServices/accounts/acc",
                    "name": "acc",
                    "kind": "AIServices",
                    "properties": {
                        "endpoints": {"AI Foundry API": "https://acc.services.ai.azure.com"}
                    },
                }
            ]

        def list_projects(self, account_id):
            return [{"id": "/subscriptions/sub1/.../projects/p1", "name": "acc/p1"}]

        def list_agents(self, endpoint):
            assert endpoint == "https://acc.services.ai.azure.com/api/projects/p1"
            return [{"id": "ag1", "name": "Agent One", "instructions": "hi", "tools": []}]

        def close(self):
            pass

    monkeypatch.setattr(fdy, "FoundryClient", FakeClient)
    from agent_census.sources.foundry import FoundryLiveSource

    result = FoundryLiveSource(FakeAuth()).scan()
    assert result.meta.source == "foundry"
    assert result.summary.total_agents == 1
    assert result.agents[0].source_system.value == "azure_ai_foundry"
    assert result.agents[0].external_id.endswith(":agent:ag1")
