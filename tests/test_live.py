"""Live Copilot Studio path covered with a mocked HTTP transport (no network)."""

import httpx

from agent_census.live.dataverse import DataverseClient, _scope_for


class FakeAuth:
    def get_token(self, scope: str) -> str:
        return "fake-token"


def test_scope_for():
    assert _scope_for("https://org.api.crm.dynamics.com") == "https://org.crm.dynamics.com/.default"
    assert (
        _scope_for("https://org.api.crm4.dynamics.com") == "https://org.crm4.dynamics.com/.default"
    )


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if "globaldisco" in request.url.host:
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "UniqueName": "env1",
                        "FriendlyName": "Env One",
                        "ApiUrl": "https://org.api.crm.dynamics.com",
                    },
                ]
            },
        )
    if path.endswith("/bots"):
        return httpx.Response(
            200,
            json={
                "value": [
                    {"botid": "b1", "name": "Bot One", "statecode": 0, "schemaname": "new_bot1"},
                ]
            },
        )
    if path.endswith("/botcomponents"):
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "componenttype": 16,
                        "name": "KB",
                        "_parentbotid_value": "b1",
                        "data": "sharepoint",
                    },
                ]
            },
        )
    return httpx.Response(404)


def _fake_client(monkeypatch) -> DataverseClient:
    c = DataverseClient.__new__(DataverseClient)
    c._auth = FakeAuth()
    c._http = httpx.Client(transport=httpx.MockTransport(_handler))
    return c


def test_client_environment_and_bot_flow(monkeypatch):
    c = _fake_client(monkeypatch)
    envs = c.list_environments()
    assert envs[0]["unique_name"] == "env1"
    bots = c.list_bots(envs[0]["api_url"])
    assert bots[0]["name"] == "Bot One"
    comps = c.list_botcomponents(envs[0]["api_url"])
    assert comps[0]["componenttype"] == 16
    c.close()


def test_live_source_assembles_result(monkeypatch):
    import agent_census.live.dataverse as dv

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def list_environments(self):
            return [
                {
                    "unique_name": "env1",
                    "friendly_name": "Env One",
                    "api_url": "https://org.api.crm.dynamics.com",
                }
            ]

        def list_bots(self, api_url):
            return [
                {
                    "botid": "b1",
                    "name": "Public Bot",
                    "statecode": 0,
                    "schemaname": "s1",
                    "accesscontrolpolicy": 0,
                    "authenticationmode": 1,
                }
            ]

        def list_botcomponents(self, api_url):
            return []

        def close(self):
            pass

    monkeypatch.setattr(dv, "DataverseClient", FakeClient)
    from agent_census.sources.copilot_studio import CopilotStudioLiveSource

    result = CopilotStudioLiveSource(FakeAuth()).scan()
    assert result.meta.source == "copilot_studio"
    assert result.summary.total_agents == 1
    agent = result.agents[0]
    assert agent.no_auth_required and agent.shared_with_everyone
    assert any(f.rule_id == "SWEEP-001" for f in agent.findings)
