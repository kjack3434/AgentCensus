"""Google Cloud discovery — normalize, client (mocked), source, auth, CLI guards.

No network: the client tests use httpx.MockTransport and the source test injects a
fake client. google-auth is never imported (the optional 'gcp' extra is not used).
"""

import re
import types

import httpx
import pytest
from typer.testing import CliRunner

from agent_census.cli import app
from agent_census.errors import DiscoveryError
from agent_census.live import auth
from agent_census.live.gcp import ApiNotEnabled, GcpClient, _retry_delay
from agent_census.models import AgentKind, KnowledgeKind, SourceSystem, ToolType
from agent_census.normalize import (
    gcp_agentspace_agent_to_agent,
    gcp_dialogflow_to_agent,
    gcp_reasoning_engine_to_agent,
)

runner = CliRunner()
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_WIDE = {"COLUMNS": "200"}


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


class FakeAuth:
    def get_token(self, scope: str) -> str:
        return "fake-token"


# ── normalize ────────────────────────────────────────────────────────────────


def test_reasoning_engine_is_opaque_but_inventoried():
    raw = {
        "name": "projects/p/locations/us-central1/reasoningEngines/123",
        "displayName": "Returns Agent",
        "description": "handles returns",
        "createTime": "2026-02-10T10:00:00Z",
        "updateTime": "2026-06-12T08:30:00Z",
        "spec": {"classMethods": [{"name": "query"}], "packageSpec": {"pythonVersion": "3.12"}},
    }
    a = gcp_reasoning_engine_to_agent(raw, project="p", location="us-central1")
    assert a.source_system is SourceSystem.VERTEX_AI_AGENT_ENGINE
    assert a.provider == "google"
    assert a.kind is AgentKind.HOSTED
    assert a.external_id == "projects/p/locations/us-central1/reasoningEngines/123"
    assert a.model is None and a.instructions == ""
    # model / instructions / tools are genuinely not exposed
    assert set(a.properties["unobservable"]) == {"model", "instructions", "tools"}
    # timestamps ARE exposed, so staleness can still apply
    assert a.created_on is not None and a.modified_on is not None


def test_reasoning_engine_captures_adk_deployment_signal():
    # The sourceCodeSpec (ADK) shape exposes framework, entrypoint, identity, and
    # telemetry config — all captured even though behavior stays unobservable.
    raw = {
        "name": "projects/p/locations/us-west1/reasoningEngines/999",
        "displayName": "My Agent",
        "spec": {
            "agentFramework": "google-adk",
            "sourceCodeSpec": {
                "pythonSpec": {"entrypointModule": "main", "entrypointObject": "app"}
            },
            "effectiveIdentity": "svc@p.iam.gserviceaccount.com",
            "deploymentSpec": {
                "env": [
                    {"name": "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY", "value": "true"},
                    {"name": "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "value": "true"},
                ]
            },
        },
    }
    a = gcp_reasoning_engine_to_agent(raw, project="p", location="us-west1")
    assert a.properties["framework"] == "google_adk"
    assert a.properties["entrypoint"] == "main:app"
    assert a.properties["telemetry_enabled"] is True
    assert a.properties["behavior_recoverable_via_trace"] is True
    assert a.owners and a.owners[0].email == "svc@p.iam.gserviceaccount.com"
    # still suppressed for scoring
    assert set(a.properties["unobservable"]) == {"model", "instructions", "tools"}


def test_reasoning_engine_framework_hint_fallback():
    # No agentFramework field -> fall back to scanning the spec blob.
    raw = {"name": "x/reasoningEngines/1", "spec": {"classMethods": [{"name": "google.adk.run"}]}}
    a = gcp_reasoning_engine_to_agent(raw, project="p", location="us-central1")
    assert a.properties.get("framework") == "google_adk"


def test_reasoning_engine_enriched_by_lowcode_config():
    # When the no-code (Agent Designer) config is available, the real model /
    # instruction / tools are applied and nothing stays unobservable.
    raw = {"name": "projects/p/locations/us-west1/reasoningEngines/678", "displayName": "Test"}
    lowcode = {
        "name": "projects/p/locations/us-west1/lowcodeAgents/agent_178",
        "rootAgentId": "agent_178",
        "agentEngineInstanceName": "projects/p/locations/us-west1/reasoningEngines/678",
        "nodes": [
            {
                "id": "agent_178",
                "displayName": "Test",
                "llmAgent": {
                    "model": "gemini-3.5-flash",
                    "instruction": "You are a demo returns assistant.",
                    "tools": [{"googleSearchTool": {}}, {"urlContextTool": {}}],
                },
            }
        ],
    }
    a = gcp_reasoning_engine_to_agent(raw, project="p", location="us-west1", lowcode=lowcode)
    assert a.model == "gemini-3.5-flash"
    assert a.instructions == "You are a demo returns assistant."
    assert {t.name for t in a.tools} == {"Google Search", "URL Context"}
    assert "unobservable" not in a.properties  # fully observed now
    assert a.properties["source_config"] == "lowcode_agent"
    # a real model + non-empty instructions -> not flagged ungoverned/empty
    from agent_census.findings import evaluate

    rules = {f.rule_id for f in evaluate(a)}
    assert "SWEEP-007" not in rules and "SWEEP-009" not in rules


def test_dialogflow_instructions_and_tools():
    agent_raw = {"name": "projects/p/locations/global/agents/abc", "displayName": "Orders"}
    playbooks = [
        {
            "goal": "Help with orders",
            "instruction": {"steps": [{"text": "Confirm the order id"}, {"text": "Then act"}]},
        }
    ]
    tools = [
        {"name": "x/tools/1", "displayName": "UpdateAddress", "openApiSpec": {"textSchema": "..."}},
        {"name": "x/tools/2", "displayName": "LookupOrder", "functionSpec": {}},
        {"name": "x/tools/3", "displayName": "OrdersKB", "dataStoreSpec": {"dataStore": "ds"}},
    ]
    a = gcp_dialogflow_to_agent(agent_raw, playbooks, tools, project="p", location="global")
    assert a.source_system is SourceSystem.DIALOGFLOW_CX
    assert "Confirm the order id" in a.instructions and "Help with orders" in a.instructions
    types_ = {t.tool_type for t in a.tools}
    assert ToolType.OPENAPI in types_ and ToolType.FUNCTION in types_
    # the data-store tool becomes knowledge, not a tool
    assert any(k.kind is KnowledgeKind.DISCOVERY_ENGINE_DATASTORE for k in a.knowledge)
    assert len(a.tools) == 2
    # model is unobservable, but instructions/tools are real (not suppressed)
    assert a.properties["unobservable"] == ["model"]
    assert any(t.write_capable for t in a.tools)  # "UpdateAddress" verb -> write


def test_agentspace_managed_agent_is_inventoried():
    engine = {
        "name": "projects/p/locations/global/collections/default_collection/engines/ge1",
        "displayName": "Gemini Enterprise",
        "dataStoreIds": ["sharepoint-hr", "gdrive-policies"],
    }
    agent = {
        "name": (
            "projects/200/locations/global/collections/default_collection/engines/ge1"
            "/assistants/default_assistant/agents/deep_research"
        ),
        "displayName": "Deep Research",
        "description": "Gathers and analyzes information from internal and external sources.",
        "managedAgentDefinition": {"researchAssistantAgentConfig": {}},
        "state": "ENABLED",
        "sharingConfig": {"scope": "ALL_USERS"},
        "createTime": "2026-06-20T12:24:29Z",
    }
    a = gcp_agentspace_agent_to_agent(agent, project="p", location="global", engine=engine)
    assert a.source_system is SourceSystem.AGENTSPACE
    assert a.name == "Deep Research"
    assert a.shared_with_everyone is True  # ALL_USERS
    assert a.properties["subtype"] == "managedAgent"
    assert a.properties["engine"] == "ge1"
    # engine grounding stores are app-level (shared); the SharePoint one is external
    assert all(k.assignment == "app" for k in a.knowledge)
    ext = [k for k in a.knowledge if k.external_source]
    assert len(ext) == 1
    assert ext[0].external_source == "sharepoint"
    assert ext[0].name == "sharepoint-hr"
    assert set(a.properties["unobservable"]) == {"model", "instructions", "tools"}


def test_agentspace_invocation_mode_is_posture_only():
    from agent_census.findings import evaluate

    agent = {
        "name": "projects/p/locations/global/.../agents/auto",
        "displayName": "Auto Agent",
        "managedAgentDefinition": {},
        "sharingConfig": {"scope": "ALL_USERS"},
        "agentInvocationSpec": {"invocationMode": "AUTOMATIC"},
    }
    a = gcp_agentspace_agent_to_agent(agent, project="p", location="global")
    # AUTOMATIC is recorded for visibility but NOT treated as autonomy for GCP.
    assert a.autonomous is False
    assert a.properties["invocation_mode"] == "AUTOMATIC"
    rules = {f.rule_id for f in evaluate(a)}
    assert "SWEEP-004" not in rules  # not autonomous -> no finding
    assert "SWEEP-011" not in rules  # shared, but no amplifier -> not flagged


def test_agentspace_agent_enriched_from_provisioned_engine():
    re_name = "projects/p/locations/us-west1/reasoningEngines/678"
    agent = {
        "name": "projects/p/locations/global/.../agents/wrap",
        "displayName": "Wrapped Agent",
        "adkAgentDefinition": {"provisionedReasoningEngine": {"reasoningEngine": re_name}},
        "sharingConfig": {"scope": "ALL_USERS"},
    }
    lowcode = {
        "name": "projects/p/locations/us-west1/lowcodeAgents/agent_178",
        "rootAgentId": "agent_178",
        "agentEngineInstanceName": re_name,
        "nodes": [
            {
                "id": "agent_178",
                "displayName": "My Design",
                "llmAgent": {
                    "model": "gemini-3.5-flash",
                    "instruction": "do the thing",
                    "tools": [{"googleSearchTool": {}}],
                },
            }
        ],
    }
    a = gcp_agentspace_agent_to_agent(
        agent, project="p", location="global", lowcode_by_engine={"678": lowcode}
    )
    # behavior grafted from the underlying agent, attributed via behavior_source
    assert a.model == "gemini-3.5-flash"
    assert a.instructions == "do the thing"
    assert {t.name for t in a.tools} == {"Google Search"}
    assert "unobservable" not in a.properties
    bs = a.properties["behavior_source"]
    assert bs["kind"] == "agent_engine"
    assert bs["engine_id"] == "678"
    assert bs["name"] == "My Design"  # friendly name rendered in provenance
    # the Gemini agent stays primary (keyed by the runtime it wraps)
    assert a.external_id == re_name


def test_dialogflow_captures_timestamps():
    agent_raw = {
        "name": "projects/p/locations/global/agents/abc",
        "displayName": "Orders",
        "updateTime": "2026-06-01T00:00:00Z",
    }
    a = gcp_dialogflow_to_agent(agent_raw, [], [], project="p", location="global")
    assert a.modified_on is not None


def test_agentspace_data_store_friendly_name():
    engine = {
        "name": "projects/p/locations/global/collections/default_collection/engines/ge1",
        "dataStoreIds": ["notebooklm-datastore_178"],
    }
    agent = {"name": "x/agents/a", "displayName": "App Agent", "managedAgentDefinition": {}}
    data_stores = {"notebooklm-datastore_178": {"displayName": "notebookLM-DataStore"}}
    a = gcp_agentspace_agent_to_agent(
        agent, project="p", location="global", engine=engine, data_stores=data_stores
    )
    assert len(a.knowledge) == 1
    k = a.knowledge[0]
    assert k.name == "notebookLM-DataStore"  # friendly name, not the raw id
    assert k.connection_reference == "notebooklm-datastore_178"  # id retained
    assert k.assignment == "app"  # engine data store is app-level, not agent-owned
    assert k.external_source is None  # native Google store, not external


def test_agentspace_agent_dedups_against_provisioned_engine():
    # A custom ADK agent backed by an Agent Engine resource keys off that engine
    # so it isn't double-counted across the two surfaces.
    re_name = "projects/p/locations/us-central1/reasoningEngines/123"
    agent = {
        "name": "projects/p/locations/global/.../agents/custom",
        "displayName": "Custom ADK",
        "adkAgentDefinition": {"provisionedReasoningEngine": {"reasoningEngine": re_name}},
    }
    a = gcp_agentspace_agent_to_agent(agent, project="p", location="global")
    assert a.external_id == re_name


# ── findings suppression ─────────────────────────────────────────────────────


def test_unobservable_fields_are_not_scored():
    from agent_census.findings import evaluate

    engine = gcp_reasoning_engine_to_agent(
        {"name": "x/reasoningEngines/1", "displayName": "Opaque"},
        project="p",
        location="us-central1",
    )
    rules = {f.rule_id for f in evaluate(engine)}
    # empty model + empty instructions would normally trip 007 + 009 — suppressed here
    assert "SWEEP-007" not in rules
    assert "SWEEP-009" not in rules


# ── client (mocked transport) ────────────────────────────────────────────────


def _client(handler, auth=None) -> GcpClient:
    c = GcpClient.__new__(GcpClient)
    c._auth = auth or FakeAuth()
    c._http = httpx.Client(transport=httpx.MockTransport(handler))
    c._send_quota = bool(getattr(c._auth, "needs_quota_project", False))
    return c


class QuotaAuth(FakeAuth):
    needs_quota_project = True


def test_quota_project_header_sent_for_user_creds():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["xgup"] = request.headers.get("x-goog-user-project")
        return httpx.Response(200, json={"reasoningEngines": []})

    c = _client(handler, auth=QuotaAuth())
    c.list_reasoning_engines("demo-project", "us-central1")
    assert seen["xgup"] == "demo-project"
    c.close()


def test_quota_project_header_omitted_for_service_account():
    seen = {"xgup": "unset"}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["xgup"] = request.headers.get("x-goog-user-project")
        return httpx.Response(200, json={"reasoningEngines": []})

    c = _client(handler)  # FakeAuth has no needs_quota_project -> header omitted
    c.list_reasoning_engines("p", "us-central1")
    assert seen["xgup"] is None
    c.close()


def test_quota_project_403_is_not_benign():
    # A "requires a quota project" 403 (reason SERVICE_DISABLED) must NOT be
    # swallowed as ApiNotEnabled — it's a real misconfig worth surfacing.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "error": {
                    "status": "PERMISSION_DENIED",
                    "message": "The discoveryengine.googleapis.com API requires a quota project",
                    "details": [{"reason": "SERVICE_DISABLED"}],
                }
            },
        )

    c = _client(handler)
    with pytest.raises(DiscoveryError) as exc:
        c.list_agentspace_engines("p", "global")
    assert not isinstance(exc.value, ApiNotEnabled)
    c.close()


def test_client_paginates_and_filters_scratch_engines():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("pageToken") == "p2":
            return httpx.Response(
                200,
                json={
                    "reasoningEngines": [
                        {"name": "x/reasoningEngines/2", "displayName": "Two"},
                        {
                            "name": "x/reasoningEngines/3",
                            "displayName": "AGENT_DESIGNER_GENERATED_DO_NOT_DELETE",
                        },
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "reasoningEngines": [{"name": "x/reasoningEngines/1", "displayName": "One"}],
                "nextPageToken": "p2",
            },
        )

    c = _client(handler)
    engines = c.list_reasoning_engines("p", "us-central1")
    names = {e["displayName"] for e in engines}
    assert names == {"One", "Two"}  # scratch engine filtered out
    c.close()


def test_client_service_disabled_is_benign():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "error": {
                    "code": 403,
                    "status": "PERMISSION_DENIED",
                    "message": "Vertex AI API has not been used in project 1 or it is disabled",
                    "details": [{"reason": "SERVICE_DISABLED", "domain": "googleapis.com"}],
                }
            },
        )

    c = _client(handler)
    with pytest.raises(ApiNotEnabled):
        c.list_reasoning_engines("p", "us-central1")
    c.close()


def test_client_permission_denied_is_genuine():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"error": {"status": "PERMISSION_DENIED", "message": "caller lacks permission"}},
        )

    c = _client(handler)
    with pytest.raises(DiscoveryError) as exc:
        c.list_dialogflow_agents("p", "global")
    assert not isinstance(exc.value, ApiNotEnabled)
    c.close()


def test_client_404_is_benign():
    c = _client(lambda req: httpx.Response(404))
    with pytest.raises(ApiNotEnabled):
        c.list_agentspace_engines("p", "global")
    c.close()


def test_retry_delay_handles_non_numeric_retry_after():
    # numeric Retry-After honored, capped by exponential backoff
    assert _retry_delay(0, "0.5") == 0.5
    assert _retry_delay(0, "30") == 1.0  # min(2**0, 30)
    assert _retry_delay(1, None) == 2.0  # default
    # an HTTP-date (or any non-numeric) must NOT crash float() — fall back to 2
    assert _retry_delay(0, "Wed, 21 Oct 2026 07:28:00 GMT") == 1.0  # min(1, 2)
    assert _retry_delay(3, "Wed, 21 Oct 2026 07:28:00 GMT") == 2.0  # min(8, 2)


def test_client_retries_on_http_date_retry_after(monkeypatch):
    import agent_census.live.gcp as gcp

    monkeypatch.setattr(gcp.time, "sleep", lambda *_a, **_k: None)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:  # date-format Retry-After used to crash float()
            return httpx.Response(503, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
        return httpx.Response(200, json={"reasoningEngines": []})

    c = _client(handler)
    assert c.list_reasoning_engines("p", "us-central1") == []  # retried, didn't crash
    assert calls["n"] == 2
    c.close()


# ── source assembly ──────────────────────────────────────────────────────────


def test_gcp_source_assembles_result(monkeypatch):
    import agent_census.live.gcp as gcp

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def list_reasoning_engines(self, project, location):
            # same engine returned for every region -> exercises external_id dedup
            return [
                {
                    "name": "projects/p/locations/us-central1/reasoningEngines/e1",
                    "displayName": "Engine One",
                }
            ]

        def list_lowcode_agents(self, project, location):
            if location != "us-central1":
                return []
            return [
                {
                    "name": "projects/p/locations/us-central1/lowcodeAgents/agent_x",
                    "rootAgentId": "agent_x",
                    "agentEngineInstanceName": (
                        "projects/p/locations/us-central1/reasoningEngines/e1"
                    ),
                    "nodes": [
                        {
                            "id": "agent_x",
                            "llmAgent": {
                                "model": "gemini-3.5-flash",
                                "instruction": "be helpful",
                                "tools": [{"googleSearchTool": {}}],
                            },
                        }
                    ],
                }
            ]

        def list_dialogflow_agents(self, project, location):
            if location != "global":
                return []
            return [{"name": "projects/p/locations/global/agents/a1", "displayName": "Bot"}]

        def list_dialogflow_playbooks(self, resource, location, project):
            return [{"goal": "do things", "instruction": {"steps": [{"text": "step one"}]}}]

        def list_dialogflow_tools(self, resource, location, project):
            return [{"displayName": "DeletePayment", "openApiSpec": {}}]

        def list_agentspace_engines(self, project, location):
            if location != "global":  # Discovery Engine multi-region; app is global here
                return []
            return [
                {
                    "name": (
                        "projects/p/locations/global/collections/default_collection/engines/ge"
                    ),
                    "displayName": "Gemini Enterprise",
                }
            ]

        def list_data_stores(self, project, location):
            return []

        def list_agentspace_assistants(self, engine_resource, location, project):
            return [{"name": engine_resource + "/assistants/default_assistant"}]

        def list_agentspace_agents(self, assistant_resource, location, project):
            return [
                {
                    "name": assistant_resource + "/agents/deep_research",
                    "displayName": "Deep Research",
                    "managedAgentDefinition": {},
                    "sharingConfig": {"scope": "ALL_USERS"},
                    "state": "ENABLED",
                }
            ]

        def close(self):
            pass

    monkeypatch.setattr(gcp, "GcpClient", FakeClient)
    from agent_census.sources.gcp import GcpLiveSource

    result = GcpLiveSource(FakeAuth(), projects=["p"]).scan()
    assert result.meta.source == "gcp"
    assert result.meta.environment == "p"
    assert result.summary.total_agents == 3  # engine + dialogflow + agentspace agent (deduped)
    srcs = {a.source_system.value for a in result.agents}
    assert srcs == {"vertex_ai_agent_engine", "dialogflow_cx", "agentspace"}
    engine = next(a for a in result.agents if a.source_system.value == "vertex_ai_agent_engine")
    assert engine.model == "gemini-3.5-flash"  # enriched from the lowcodeAgent join
    assert engine.instructions == "be helpful"
    bot = next(a for a in result.agents if a.source_system.value == "dialogflow_cx")
    bot_rules = {f.rule_id for f in bot.findings}
    assert "SWEEP-005" in bot_rules  # write-capable tool, no approval gate
    assert "SWEEP-007" not in bot_rules  # model unobservable
    ge = next(a for a in result.agents if a.source_system.value == "agentspace")
    assert ge.name == "Deep Research" and ge.shared_with_everyone is True


def test_gcp_source_warns_on_real_error(monkeypatch):
    import agent_census.live.gcp as gcp

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def list_reasoning_engines(self, project, location):
            raise DiscoveryError("access denied (403)")

        def list_lowcode_agents(self, project, location):
            return []

        def list_data_stores(self, project, location):
            return []

        def list_dialogflow_agents(self, project, location):
            raise ApiNotEnabled("not enabled")  # benign -> no warning

        def list_agentspace_engines(self, project, location):
            return []

        def close(self):
            pass

    monkeypatch.setattr(gcp, "GcpClient", FakeClient)
    from agent_census.sources.gcp import GcpLiveSource

    result = GcpLiveSource(FakeAuth(), projects=["p"], locations=["us-central1"]).scan()
    assert result.summary.total_agents == 0
    # the genuine error is surfaced; the disabled API is not
    assert any("agent_engine" in w for w in result.warnings)


def test_gcp_source_requires_a_project():
    from agent_census.sources.gcp import GcpLiveSource

    with pytest.raises(DiscoveryError) as exc:
        GcpLiveSource(FakeAuth(), projects=[]).scan()
    assert "project" in str(exc.value).lower()


# ── auth ─────────────────────────────────────────────────────────────────────


def test_ssrf_guard_on_service_account_token_uri():
    auth._validate_google_token_uri("https://oauth2.googleapis.com/token")  # ok
    auth._validate_google_token_uri(None)  # ok
    with pytest.raises(DiscoveryError):
        auth._validate_google_token_uri("https://evil.example/token")


def test_build_gcp_auth_cli(monkeypatch):
    # gcloud present and signed-in (probe succeeds) -> provider + note
    monkeypatch.setattr(auth.shutil, "which", lambda name: "/usr/bin/gcloud")
    monkeypatch.setattr(
        auth.subprocess,
        "run",
        lambda cmd, **kw: types.SimpleNamespace(returncode=0, stdout="tok\n", stderr=""),
    )
    provider, note = auth.build_gcp_auth("cli")
    assert isinstance(provider, auth.GcloudCliAuth)
    assert "gcloud" in note


def test_build_gcp_auth_cli_missing(monkeypatch):
    monkeypatch.setattr(auth.shutil, "which", lambda name: None)
    provider, note = auth.build_gcp_auth("cli")
    assert provider is None and "gcloud" in note


def test_build_gcp_auth_app_requires_key():
    provider, note = auth.build_gcp_auth("app")
    assert provider is None and "gcp-key-file" in note


def test_build_microsoft_auth_device_requires_client_id():
    provider, note = auth.build_microsoft_auth("device")
    assert provider is None and "client-id" in note


def test_build_microsoft_auth_cli_missing_az(monkeypatch):
    monkeypatch.setattr(auth.shutil, "which", lambda name: None)
    provider, note = auth.build_microsoft_auth("cli")
    assert provider is None and "Azure CLI" in note


def test_gcloud_token_is_cached(monkeypatch):
    monkeypatch.setattr(auth.shutil, "which", lambda _: "/usr/bin/gcloud")
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return types.SimpleNamespace(returncode=0, stdout="tok123\n", stderr="")

    monkeypatch.setattr(auth.subprocess, "run", fake_run)
    provider = auth.GcloudCliAuth()
    assert provider.get_token("scope-a") == "tok123"
    assert provider.get_token("scope-b") == "tok123"
    assert len(calls) == 1  # one broad token, reused


def test_gcloud_impersonation_omits_quota_header(monkeypatch):
    # Plain gcloud = user creds -> send x-goog-user-project; impersonation = SA
    # token -> omit it (else a Viewer-only SA can 403 on the header).
    monkeypatch.setattr(auth.shutil, "which", lambda name: "/usr/bin/gcloud")
    assert auth.GcloudCliAuth().needs_quota_project is True
    impersonated = auth.GcloudCliAuth(impersonate="svc@p.iam.gserviceaccount.com")
    assert impersonated.needs_quota_project is False


def _fake_adc(monkeypatch, creds):
    fake_requests = types.SimpleNamespace(Request=lambda: object())
    fake_google_auth = types.SimpleNamespace(default=lambda scopes=None: (creds, "proj"))
    mods = {"google.auth.transport.requests": fake_requests, "google.auth": fake_google_auth}
    monkeypatch.setattr(auth.importlib, "import_module", lambda name: mods[name])


def test_adc_user_creds_send_quota(monkeypatch):
    class _UserCreds:  # user creds have no service_account_email
        pass

    _fake_adc(monkeypatch, _UserCreds())
    assert auth.google_adc_auth().needs_quota_project is True


def test_adc_service_account_omits_quota(monkeypatch):
    class _SACreds:  # service-account / compute creds expose this
        service_account_email = "svc@p.iam.gserviceaccount.com"

    _fake_adc(monkeypatch, _SACreds())
    assert auth.google_adc_auth().needs_quota_project is False


# ── CLI strategy / multi-cloud ───────────────────────────────────────────────


def test_cli_gcp_with_device_strategy_skips(tmp_path):
    # device is Microsoft-only -> GCP has no auth -> nothing runnable -> exit 2
    res = runner.invoke(
        app,
        ["sweep", "--source", "gcp", "--auth", "device", "-o", str(tmp_path / "r.html")],
        env=_WIDE,
    )
    assert res.exit_code == 2
    assert "device" in _plain(res.output).lower()


def test_cli_microsoft_with_adc_strategy_skips(tmp_path):
    # adc is Google-only -> Microsoft has no auth -> nothing runnable -> exit 2
    res = runner.invoke(
        app,
        ["sweep", "--source", "foundry", "--auth", "adc", "-o", str(tmp_path / "r.html")],
        env=_WIDE,
    )
    assert res.exit_code == 2
    assert "google-only" in _plain(res.output).lower()
