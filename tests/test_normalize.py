from agent_census.models import AgentKind, ToolType
from agent_census.normalize import copilot_bot_to_agent, infer_write_capable


def _bot(**kw):
    base = {
        "botid": "11111111-1111-1111-1111-111111111111",
        "name": "Field Helpdesk",
        "schemaname": "new_fieldhelpdesk",
        "statecode": 0,
        "accesscontrolpolicy": 3,
        "authenticationmode": 1,
        "configuration": (
            '{"channels":[{"channelId":"msteams"},{"channelId":"slack"},{"channelId":"webchat"}]}'
        ),
        "_ownerid_value": "22222222-2222-2222-2222-222222222222",
        "_ownerid_value@OData.Community.Display.V1.FormattedValue": "Dana Owner",
        "createdon": "2026-01-01T00:00:00Z",
        "modifiedon": "2026-06-01T00:00:00Z",
        "template": "gpt",
    }
    base.update(kw)
    return base


_COMPONENTS = [
    {
        "componenttype": 15,
        "description": "Helps field techs",
        "data": (
            "modelNameHint: gpt-4o\n"
            "instructions: |\n  You are a field helpdesk agent.\n"
            "webBrowsing: true\ncontentModeration: High"
        ),
    },
    {"componenttype": 16, "name": "Service Manuals", "data": "kind: AzureSearchIndex"},
    {"componenttype": 17, "data": "external trigger"},
    {"componenttype": 9, "data": "step: HttpRequestAction to finance system"},
]


def test_normalize_full_bot():
    a = copilot_bot_to_agent(_bot(), _COMPONENTS, org="contosoenv")
    assert a.name == "Field Helpdesk"
    assert a.external_id == "contosoenv:bot:new_fieldhelpdesk"
    assert a.kind is AgentKind.DECLARATIVE
    assert a.model == "GPT-4o"
    assert a.shared_with_everyone and a.multi_tenant and a.no_auth_required
    assert a.autonomous is True
    assert a.channels == ["Microsoft Teams", "Slack", "Web Chat"]
    assert "You are a field helpdesk agent." in a.instructions
    tool_types = {t.tool_type for t in a.tools}
    assert ToolType.WEB_BROWSE in tool_types
    assert ToolType.HTTP in tool_types
    assert any(t.write_capable for t in a.tools if t.tool_type is ToolType.HTTP)
    assert a.knowledge[0].name == "Service Manuals"
    assert a.guardrails and a.guardrails[0].level == "high"
    assert a.owners[0].name == "Dana Owner"


def test_normalize_minimal_inactive_custom():
    a = copilot_bot_to_agent({"name": "B", "botid": "x", "statecode": 1})
    assert a.kind is AgentKind.CUSTOM
    assert a.status == "inactive"
    assert a.channels == []
    assert a.owners == []


def test_write_heuristic():
    assert infer_write_capable("CreatePayment", ToolType.FUNCTION) is True
    assert infer_write_capable("Lookup", ToolType.FUNCTION) is False
    assert infer_write_capable("anything", ToolType.HTTP) is True
    assert infer_write_capable("read-only", ToolType.CONNECTOR_ACTION) is True
