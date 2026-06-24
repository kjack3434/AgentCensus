"""Map raw Dataverse records into the flat :class:`Agent` model.

This is the single Copilot Studio normalization path, shared by the live source
and exercised directly by tests. Component ``data`` is YAML-ish, so we use
light, dependency-free line/marker scanning rather than a YAML parser.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .live.constants import (
    ACCESS_ANY,
    ACCESS_MULTI_TENANT,
    ACTION_MARKERS,
    AUTH_NONE,
    CHANNEL_DISPLAY,
    COMPONENT_BOT_DEFINITION,
    COMPONENT_EXTERNAL_TRIGGER,
    COMPONENT_KNOWLEDGE,
    COMPONENT_TOPIC,
    DEFAULT_MODEL_DISPLAY,
    MODEL_HINT_DISPLAY,
)
from .models import (
    Agent,
    AgentKind,
    GuardrailRef,
    KnowledgeKind,
    KnowledgeRef,
    OwnerRef,
    SourceSystem,
    ToolRef,
    ToolType,
)

_OWNER_FORMATTED = "_ownerid_value@OData.Community.Display.V1.FormattedValue"
_WRITE_VERBS = re.compile(
    r"(create|update|delete|send|post|write|approve|pay|provision|insert|remove)", re.IGNORECASE
)


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


_WRITE_TOOL_TYPES = (ToolType.HTTP, ToolType.CONNECTOR_ACTION, ToolType.CODE_INTERPRETER)


def infer_write_capable(name: str, tool_type: ToolType) -> bool:
    if tool_type in _WRITE_TOOL_TYPES:
        return True
    return bool(_WRITE_VERBS.search(name or ""))


def _scan(data: str, pattern: str) -> str | None:
    m = re.search(pattern, data or "", re.IGNORECASE)
    return m.group(1).strip().strip("\"'") if m else None


def _scan_block(data: str, key: str) -> str:
    """Extract a YAML scalar/block for ``key`` (inline value or indented block)."""
    lines = (data or "").splitlines()
    for i, line in enumerate(lines):
        m = re.match(rf"(\s*){re.escape(key)}:\s*(\|[-+]?|>[-+]?)?[ \t]*(.*)$", line)
        if not m:
            continue
        indent, style, inline = m.group(1), m.group(2), m.group(3)
        if inline and not style:
            return inline.strip().strip("\"'")
        base = len(indent)
        collected: list[str] = []
        for nxt in lines[i + 1 :]:
            if not nxt.strip():
                collected.append("")
                continue
            if (len(nxt) - len(nxt.lstrip())) <= base:
                break
            collected.append(nxt[base:].rstrip())
        return "\n".join(collected).strip()
    return ""


def _channels(configuration: Any) -> list[str]:
    try:
        cfg = json.loads(configuration) if isinstance(configuration, str) else (configuration or {})
    except (json.JSONDecodeError, TypeError):
        return []
    out: list[str] = []
    for ch in cfg.get("channels", []) or []:
        if isinstance(ch, dict):
            cid = str(ch.get("channelId") or ch.get("id") or "").lower()
        else:
            cid = str(ch).lower()
        if not cid:
            continue
        out.append(CHANNEL_DISPLAY.get(cid, cid.replace("-", " ").title()))
    return out


def _model_from_hint(hint: str | None) -> tuple[str, str]:
    if not hint:
        return DEFAULT_MODEL_DISPLAY, "platform_default"
    key = hint.strip().lower()
    display = MODEL_HINT_DISPLAY.get(key, hint.strip())
    tier = "standard"
    if "preview" in key:
        tier = "preview"
    elif "experimental" in key:
        tier = "experimental"
    return display, tier


def _knowledge_kind(data: str) -> KnowledgeKind:
    d = (data or "").lower()
    if "sharepoint" in d:
        return KnowledgeKind.SHAREPOINT
    if "azuresearch" in d or "azure_search" in d or "searchindex" in d or "index" in d:
        return KnowledgeKind.AZURE_SEARCH
    if "dataverse" in d or "table" in d:
        return KnowledgeKind.DATAVERSE_TABLE
    if "file" in d or "upload" in d:
        return KnowledgeKind.FILE_UPLOAD
    if "http" in d or "website" in d or "url" in d:
        return KnowledgeKind.WEB
    return KnowledgeKind.OTHER


def copilot_bot_to_agent(
    bot: dict[str, Any],
    components: list[dict[str, Any]] | None = None,
    *,
    org: str | None = None,
) -> Agent:
    components = components or []

    schema = bot.get("schemaname") or bot.get("botid") or "bot"
    external_id = f"{org or 'copilot'}:bot:{schema}"

    acl = _to_int(bot.get("accesscontrolpolicy"))
    auth = _to_int(bot.get("authenticationmode"))
    statecode = _to_int(bot.get("statecode"))

    model: str | None = None
    model_tier: str | None = None
    instructions = ""
    description = ""
    tools: list[ToolRef] = []
    knowledge: list[KnowledgeRef] = []
    guardrails: list[GuardrailRef] = []
    autonomous = False
    has_definition = False
    props: dict[str, Any] = {}

    for comp in components:
        ctype = _to_int(comp.get("componenttype"))
        data = comp.get("data") or ""

        if ctype == COMPONENT_BOT_DEFINITION:
            has_definition = True
            description = comp.get("description") or description
            instructions = _scan_block(data, "instructions") or instructions
            hint = _scan(data, r"modelNameHint:\s*['\"]?([\w.\-]+)")
            model, model_tier = _model_from_hint(hint)
            if re.search(r"webBrowsing:\s*true", data, re.IGNORECASE):
                tools.append(
                    ToolRef(
                        name="Web browsing",
                        tool_type=ToolType.WEB_BROWSE,
                        requires_approval="never",
                        source="bot_definition",
                    )
                )
            level = _scan(data, r"contentModeration:\s*['\"]?(\w+)")
            if level:
                guardrails.append(
                    GuardrailRef(kind="content_safety", level=level.lower(), source="cs_aiSettings")
                )

        elif ctype == COMPONENT_KNOWLEDGE:
            kkind = _knowledge_kind(data)
            knowledge.append(
                KnowledgeRef(
                    name=comp.get("name") or comp.get("schemaname") or "knowledge",
                    kind=kkind,
                    # Public-web grounding reaches outside the tenant's trust boundary.
                    external_source="web" if kkind == KnowledgeKind.WEB else None,
                )
            )

        elif ctype == COMPONENT_EXTERNAL_TRIGGER:
            autonomous = True
            props["external_trigger"] = True

        elif ctype == COMPONENT_TOPIC:
            for marker, (ttype, flag) in ACTION_MARKERS.items():
                if marker in data:
                    props[flag] = True
                    tname = marker.replace("Action", "")
                    tools.append(
                        ToolRef(
                            name=tname,
                            tool_type=ToolType(ttype),
                            requires_approval="unknown",
                            write_capable=infer_write_capable(tname, ToolType(ttype)),
                            source=f"topic:{marker}",
                        )
                    )

    # set write_capable on any tools whose verbs imply it
    for tool in tools:
        if not tool.write_capable:
            tool.write_capable = infer_write_capable(tool.name, tool.tool_type)

    owners: list[OwnerRef] = []
    owner_name = bot.get(_OWNER_FORMATTED)
    owner_oid = bot.get("_ownerid_value")
    if owner_name or owner_oid:
        owners.append(OwnerRef(name=owner_name, oid=owner_oid, source="copilot_studio:ownerid"))

    if description:
        props["description"] = description
    if bot.get("template"):
        props["template"] = bot.get("template")

    return Agent(
        name=bot.get("name") or schema,
        external_id=external_id,
        source_system=SourceSystem.COPILOT_STUDIO,
        kind=AgentKind.DECLARATIVE if has_definition else AgentKind.CUSTOM,
        model=model,
        model_tier=model_tier,
        instructions=instructions,
        tools=tools,
        knowledge=knowledge,
        guardrails=guardrails,
        owners=owners,
        channels=_channels(bot.get("configuration")),
        status="active" if statecode == 0 else "inactive",
        autonomous=autonomous,
        shared_with_everyone=acl in (ACCESS_ANY, ACCESS_MULTI_TENANT),
        no_auth_required=auth == AUTH_NONE,
        multi_tenant=acl == ACCESS_MULTI_TENANT,
        created_on=bot.get("createdon"),
        modified_on=bot.get("modifiedon"),
        published_on=bot.get("publishedon"),
        properties=props,
    )


# ── Azure AI Foundry ───────────────────────────────────────────────────────

_FOUNDRY_TOOL_TYPES = {
    "code_interpreter": ToolType.CODE_INTERPRETER,
    "mcp": ToolType.MCP,
    "openapi": ToolType.OPENAPI,
    "function": ToolType.FUNCTION,
    "bing_grounding": ToolType.WEB_BROWSE,
    "bing": ToolType.WEB_BROWSE,
    "browser_automation": ToolType.WEB_BROWSE,
}
# Foundry's internal definition "kind" (prompt/hosted) is an implementation detail;
# surface everything as "agent" except orchestration workflows.
_FOUNDRY_KIND = {"workflow": AgentKind.WORKFLOW}


def _approval(value: Any) -> str:
    if isinstance(value, str) and value in ("never", "always"):
        return value
    if isinstance(value, dict):
        keys = set(value)
        if keys == {"never"}:
            return "never"
        if keys == {"always"}:
            return "always"
        if "never" in keys and "always" in keys:
            return "conditional"
    return "unknown"


def _flatten_foundry(agent: dict[str, Any]) -> dict[str, Any]:
    """Flatten a Foundry agent across API generations (v2 nests under versions.latest)."""
    versions = agent.get("versions")
    latest = versions.get("latest") if isinstance(versions, dict) else None
    latest = latest if isinstance(latest, dict) else {}
    definition = latest.get("definition") or agent.get("definition") or {}
    if not isinstance(definition, dict):
        definition = {}
    return {
        "id": agent.get("id") or agent.get("name") or agent.get("assistant_id") or "",
        "name": agent.get("name") or agent.get("id") or "",
        "kind": definition.get("kind") or agent.get("kind") or "",
        "model": definition.get("model")
        or definition.get("model_deployment_name")
        or agent.get("model")
        or "",
        "instructions": definition.get("instructions") or agent.get("instructions") or "",
        "tools": definition.get("tools") or agent.get("tools") or [],
        "tool_resources": definition.get("tool_resources") or agent.get("tool_resources") or {},
        "description": latest.get("description") or agent.get("description") or "",
        "status": latest.get("status") or agent.get("status") or "active",
        "version": str(latest.get("version") or agent.get("version") or ""),
        "metadata": latest.get("metadata") or agent.get("metadata") or {},
        "rai_config": definition.get("rai_config") or agent.get("rai_config") or {},
    }


def foundry_def_to_agent(raw: dict[str, Any], *, project_external_id: str = "foundry") -> Agent:
    n = _flatten_foundry(raw)

    tools: list[ToolRef] = []
    knowledge: list[KnowledgeRef] = []
    for tool in n["tools"]:
        if not isinstance(tool, dict):
            continue
        ttype = str(tool.get("type") or "").lower()
        if ttype == "azure_ai_search":
            idx = tool.get("index_name")
            knowledge.append(
                KnowledgeRef(
                    name=tool.get("name") or "Azure AI Search",
                    kind=KnowledgeKind.AZURE_SEARCH,
                    index_name=idx,
                )
            )
            continue
        if ttype == "file_search":
            knowledge.append(
                KnowledgeRef(name=tool.get("name") or "File search", kind=KnowledgeKind.FILE_UPLOAD)
            )
            continue
        tool_type = _FOUNDRY_TOOL_TYPES.get(ttype, ToolType.OTHER)
        name = (
            tool.get("server_label")
            or tool.get("name")
            or (tool.get("function") or {}).get("name")
            or ttype
            or "tool"
        )
        tools.append(
            ToolRef(
                name=name,
                tool_type=tool_type,
                server_url=tool.get("server_url"),
                requires_approval=_approval(tool.get("require_approval")),
                write_capable=infer_write_capable(name, tool_type),
                source="foundry",
            )
        )

    # agent-level grounding (tool_resources.azure_ai_search.indexes[])
    knowledge.extend(
        KnowledgeRef(
            name=ix["index_name"], kind=KnowledgeKind.AZURE_SEARCH, index_name=ix["index_name"]
        )
        for ix in ((n["tool_resources"].get("azure_ai_search") or {}).get("indexes") or [])
        if isinstance(ix, dict) and ix.get("index_name")
    )

    guardrails: list[GuardrailRef] = []
    if n["rai_config"]:
        guardrails.append(GuardrailRef(kind="content_safety", source="foundry_rai_config"))

    model = n["model"] or None
    if not model:
        model_tier = None
    elif "preview" in model.lower():
        model_tier = "preview"
    else:
        model_tier = "standard"

    props: dict[str, Any] = {}
    if n["metadata"]:
        props["metadata"] = n["metadata"]
    if n["kind"]:
        props["realization"] = n["kind"]  # raw definition kind (prompt/hosted/...)

    return Agent(
        name=n["name"] or n["id"] or "Foundry Agent",
        external_id=f"{project_external_id}:agent:{n['id']}",
        source_system=SourceSystem.AZURE_AI_FOUNDRY,
        kind=_FOUNDRY_KIND.get(str(n["kind"]).lower(), AgentKind.AGENT),
        model=model,
        model_tier=model_tier,
        instructions=n["instructions"],
        tools=tools,
        knowledge=knowledge,
        guardrails=guardrails,
        status=str(n["status"] or "active"),
        version=n["version"] or None,
        properties=props,
    )


# ── Google Cloud ───────────────────────────────────────────────────────────
#
# Three surfaces, very different visibility. The guiding rule ("don't score what
# discovery can't see") shows up here as ``properties["unobservable"]``: a list
# of fields the API genuinely does not expose for an agent, which the finding
# rules consult so they never fire on a value that is *unknown* rather than
# *empty*. Agent Engine and Agentspace are code-deployed and opaque; only
# Dialogflow CX exposes real instructions + tools.

# Framework hints we can sometimes infer from a reasoning engine's spec blob.
_FRAMEWORK_HINTS = {
    "google.adk": "google_adk",
    "adk": "google_adk",
    "langgraph": "langgraph",
    "langchain": "langchain",
    "crewai": "crewai",
    "llama_index": "llama_index",
    "llamaindex": "llama_index",
}


def _leaf(resource: str | None) -> str:
    """Last path segment of a GCP resource name."""
    return resource.rstrip("/").split("/")[-1] if resource else ""


def _reasoning_framework(spec: dict[str, Any]) -> str | None:
    # Newer (sourceCodeSpec) deployments expose the framework as a real field.
    fw = spec.get("agentFramework")
    if isinstance(fw, str) and fw:
        return fw.replace("-", "_").lower()  # e.g. "google-adk" -> "google_adk"
    if not spec:
        return None
    blob = json.dumps(spec).lower()
    for needle, label in _FRAMEWORK_HINTS.items():
        if needle in blob:
            return label
    return None


def _engine_env(spec: dict[str, Any]) -> dict[str, str]:
    """Flatten ``spec.deploymentSpec.env`` (list of {name,value}) into a dict."""
    env = (spec.get("deploymentSpec") or {}).get("env") or []
    return {
        str(e["name"]): str(e.get("value", ""))
        for e in env
        if isinstance(e, dict) and e.get("name")
    }


# Low-code (Agent Designer) tool keys -> (ToolType, default display name).
_LOWCODE_TOOL_TYPES = {
    "googleSearchTool": (ToolType.WEB_BROWSE, "Google Search"),
    "urlContextTool": (ToolType.WEB_BROWSE, "URL Context"),
    "vertexAiSearchTool": (ToolType.FILE_SEARCH, "Vertex AI Search"),
    "codeExecutionTool": (ToolType.CODE_INTERPRETER, "Code Execution"),
    "functionTool": (ToolType.FUNCTION, "Function"),
    "openApiTool": (ToolType.OPENAPI, "OpenAPI"),
    "mcpTool": (ToolType.MCP, "MCP"),
    "agentTool": (ToolType.OTHER, "Sub-agent"),
}


def _lowcode_root_llm(lowcode: dict[str, Any]) -> dict[str, Any] | None:
    """The root node's ``llmAgent`` config (instruction / model / tools), if any."""
    root_id = lowcode.get("rootAgentId")
    nodes = [n for n in (lowcode.get("nodes") or []) if isinstance(n, dict)]
    root = next((n for n in nodes if n.get("id") == root_id), nodes[0] if nodes else None)
    cfg = root.get("llmAgent") if isinstance(root, dict) else None
    return cfg if isinstance(cfg, dict) else None


def _lowcode_tools(raw_tools: Any) -> list[ToolRef]:
    tools: list[ToolRef] = []
    for t in raw_tools or []:
        if not isinstance(t, dict):
            continue
        for key, cfg in t.items():  # each tool is a single-key dict
            ttype, default_name = _LOWCODE_TOOL_TYPES.get(key, (ToolType.OTHER, key))
            name = default_name
            if isinstance(cfg, dict):
                name = cfg.get("name") or cfg.get("displayName") or cfg.get("serverLabel") or name
            tools.append(
                ToolRef(
                    name=name,
                    tool_type=ttype,
                    requires_approval="unknown",
                    write_capable=infer_write_capable(name, ttype),
                    source="lowcode_agent",
                )
            )
            break
    return tools


def _lowcode_display_name(lowcode: dict[str, Any]) -> str:
    """Friendly name of a lowcodeAgent (root node displayName, else description)."""
    root_id = lowcode.get("rootAgentId")
    for n in lowcode.get("nodes") or []:
        if isinstance(n, dict) and n.get("id") == root_id and n.get("displayName"):
            return str(n["displayName"])
    return str(lowcode.get("description") or _leaf(lowcode.get("name")))


def _lowcode_behavior(
    lowcode: dict[str, Any] | None,
) -> tuple[str | None, str | None, str, list[ToolRef], set[str]]:
    """Pull (model, model_tier, instructions, tools, observed) from a lowcodeAgent.

    ``observed`` names the fields actually present, so callers can drop exactly
    those from their ``unobservable`` set.
    """
    cfg = _lowcode_root_llm(lowcode) if lowcode else None
    model: str | None = None
    model_tier: str | None = None
    instructions = ""
    tools: list[ToolRef] = []
    observed: set[str] = set()
    if cfg is not None:
        if cfg.get("model"):
            model = str(cfg["model"])
            ml = model.lower()
            model_tier = "preview" if "preview" in ml else "experimental" if "exp" in ml else None
            observed.add("model")
        if isinstance(cfg.get("instruction"), str):
            instructions = cfg["instruction"]
            observed.add("instructions")
        if "tools" in cfg:
            tools = _lowcode_tools(cfg.get("tools"))
            observed.add("tools")
    return model, model_tier, instructions, tools, observed


def gcp_reasoning_engine_to_agent(
    raw: dict[str, Any],
    *,
    project: str,
    location: str,
    lowcode: dict[str, Any] | None = None,
) -> Agent:
    """Vertex AI Agent Engine (reasoningEngines) — code-deployed.

    For a *code-first* deploy the model, system prompt, and tools live inside the
    package and aren't returned by the API, so they're marked unobservable rather
    than guessed (``spec.classMethods`` are session/query methods, NOT tools). What
    the API does expose is captured: framework, entry point, runtime identity, and
    telemetry config (if telemetry + content-capture are on, behavior is recoverable
    from Cloud Trace once invoked).

    For a *no-code* (Agent Designer) deploy, the matching ``lowcodeAgent`` design
    config IS available and is passed in as ``lowcode`` — its real instruction,
    model, and tools are applied and removed from the unobservable set, so the agent
    is scored on its actual behavior instead of being suppressed.
    """
    name_path = raw.get("name") or ""
    display = raw.get("displayName") or _leaf(name_path) or "Reasoning Engine"
    raw_spec = raw.get("spec")
    spec: dict[str, Any] = raw_spec if isinstance(raw_spec, dict) else {}

    props: dict[str, Any] = {
        "project": project,
        "location": location,
        "realization": "reasoning_engine",
    }

    # No-code design config (lowcodeAgent) exposes the real behavior.
    model, model_tier, instructions, tools, observed = _lowcode_behavior(lowcode)
    if observed:
        props["source_config"] = "lowcode_agent"
        props["lowcode_agent"] = _leaf(lowcode.get("name") if lowcode else None)
    unobservable = [f for f in ("model", "instructions", "tools") if f not in observed]
    if unobservable:
        props["unobservable"] = unobservable

    framework = _reasoning_framework(spec)
    if framework:
        props["framework"] = framework

    py = (spec.get("sourceCodeSpec") or {}).get("pythonSpec") or {}
    if isinstance(py, dict):
        entry = ":".join(s for s in (py.get("entrypointModule"), py.get("entrypointObject")) if s)
        if entry:
            props["entrypoint"] = entry

    env = _engine_env(spec)
    telemetry_on = env.get("GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY", "").lower() == "true"
    captures = env.get("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "").lower() == "true"
    if telemetry_on:
        props["telemetry_enabled"] = True
    if telemetry_on and captures:
        # Discovery still can't read these, but they're recoverable out-of-band.
        props["behavior_recoverable_via_trace"] = True

    if raw.get("description"):
        props["description"] = raw["description"]

    # The runtime service identity the agent acts as.
    owners: list[OwnerRef] = []
    identity = spec.get("effectiveIdentity")
    if isinstance(identity, str) and identity:
        owners.append(OwnerRef(email=identity, source="gcp:effective_identity"))

    return Agent(
        name=display,
        external_id=name_path
        or f"projects/{project}/locations/{location}/reasoningEngines/{display}",
        source_system=SourceSystem.VERTEX_AI_AGENT_ENGINE,
        provider="google",
        kind=AgentKind.HOSTED,
        model=model,
        model_tier=model_tier,
        instructions=instructions,
        tools=tools,
        owners=owners,
        created_on=raw.get("createTime"),
        modified_on=raw.get("updateTime"),
        properties=props,
    )


def _dialogflow_instructions(playbooks: list[dict[str, Any]]) -> str:
    """Build an instructions summary from playbook goal + instruction steps."""
    parts: list[str] = []
    for pb in playbooks:
        if not isinstance(pb, dict):
            continue
        goal = str(pb.get("goal") or "").strip()
        if goal:
            parts.append(goal)
        instruction = pb.get("instruction")
        steps = instruction.get("steps") if isinstance(instruction, dict) else None
        for step in steps or []:
            if isinstance(step, dict):
                text = str(step.get("text") or "").strip()
                if text:
                    parts.append(text)
    return "\n".join(parts).strip()


def gcp_dialogflow_to_agent(
    agent_raw: dict[str, Any],
    playbooks: list[dict[str, Any]] | None = None,
    tools_raw: list[dict[str, Any]] | None = None,
    *,
    project: str,
    location: str,
) -> Agent:
    """Dialogflow CX agent — the one GCP surface exposing real instructions + tools."""
    playbooks = playbooks or []
    tools_raw = tools_raw or []
    name_path = agent_raw.get("name") or ""
    display = agent_raw.get("displayName") or _leaf(name_path) or "Dialogflow Agent"

    tools: list[ToolRef] = []
    knowledge: list[KnowledgeRef] = []
    for t in tools_raw:
        if not isinstance(t, dict):
            continue
        tname = t.get("displayName") or _leaf(t.get("name")) or "tool"
        if "dataStoreSpec" in t or "dataStoreConnections" in t:
            knowledge.append(
                KnowledgeRef(name=tname, kind=KnowledgeKind.DISCOVERY_ENGINE_DATASTORE)
            )
            continue
        if "openApiSpec" in t:
            ttype = ToolType.OPENAPI
        elif "functionSpec" in t:
            ttype = ToolType.FUNCTION
        else:
            ttype = ToolType.OTHER
        tools.append(
            ToolRef(
                name=tname,
                tool_type=ttype,
                requires_approval="unknown",  # Dialogflow carries no approval gate
                write_capable=infer_write_capable(tname, ttype),
                source="dialogflow_cx",
            )
        )

    return Agent(
        name=display,
        external_id=name_path or f"projects/{project}/locations/{location}/agents/{display}",
        source_system=SourceSystem.DIALOGFLOW_CX,
        provider="google",
        kind=AgentKind.AGENT,
        model=None,  # the underlying LLM id is not exposed → suppress SWEEP-007
        instructions=_dialogflow_instructions(playbooks),
        tools=tools,
        knowledge=knowledge,
        created_on=agent_raw.get("createTime"),
        modified_on=agent_raw.get("updateTime"),
        properties={
            "project": project,
            "location": location,
            "realization": "dialogflow_cx_agent",
            "unobservable": ["model"],
        },
    )


def _agentspace_subtype(agent: dict[str, Any]) -> str:
    """Classify an Agentspace/Gemini Enterprise agent by its *Definition key."""
    for key in agent:
        if key.endswith("Definition"):
            return key[: -len("Definition")]  # managedAgent / adkAgent / dialogflowAgent / a2aAgent
    return "unknown"


# Third-party / cross-cloud data connectors (NOT native to Google) — keyword -> label.
# Conservative set to avoid false positives when scanning a data store's JSON.
_EXTERNAL_DATA_KEYWORDS = {
    "sharepoint": "sharepoint",
    "onedrive": "onedrive",
    "office365": "microsoft_365",
    "microsoft": "microsoft",
    "confluence": "confluence",
    "jira": "jira",
    "servicenow": "servicenow",
    "salesforce": "salesforce",
    "slack": "slack",
    "dropbox": "dropbox",
}


def _external_data_source(resource: dict[str, Any], ds_id: str, ds_name: str) -> str | None:
    """Name the external/cross-cloud system a data store connects to, else None.

    Best-effort: scans the data store id, display name, and resource body for known
    third-party connector keywords. Native Google stores (Drive, Vertex AI Search,
    generic) match nothing and return None.
    """
    blob = f"{ds_id} {ds_name} {json.dumps(resource or {})}".lower()
    for needle, label in _EXTERNAL_DATA_KEYWORDS.items():
        if needle in blob:
            return label
    return None


def _agentspace_provisioned_engine(agent: dict[str, Any]) -> str | None:
    """A custom ADK agent may point at a provisioned reasoning engine — dedup key."""
    for key in ("adkAgentDefinition", "managedAgentDefinition", "a2aAgentDefinition"):
        defn = agent.get(key)
        if isinstance(defn, dict):
            prov = defn.get("provisionedReasoningEngine")
            if isinstance(prov, dict) and prov.get("reasoningEngine"):
                return str(prov["reasoningEngine"])
    return None


def gcp_agentspace_agent_to_agent(
    agent: dict[str, Any],
    *,
    project: str,
    location: str,
    engine: dict[str, Any] | None = None,
    lowcode_by_engine: dict[str, dict[str, Any]] | None = None,
    data_stores: dict[str, dict[str, Any]] | None = None,
) -> Agent:
    """Agentspace / Gemini Enterprise agent (engine -> assistant -> agent).

    Covers the built-in managed agents (Deep Research, Idea Generation) and custom
    ADK / Dialogflow / A2A agents. Behavior (model / prompt / tools) is normally not
    exposed at the agent level, so it is marked unobservable — UNLESS the agent is
    backed by an Agent Engine runtime whose no-code design config (lowcodeAgent) is
    available, in which case the real behavior is grafted in and attributed to the
    underlying agent via ``properties.behavior_source`` (so the Gemini agent stays
    the primary record but shows where its behavior came from).
    """
    name_path = agent.get("name") or ""
    display = agent.get("displayName") or _leaf(name_path) or "Agentspace Agent"

    props: dict[str, Any] = {
        "project": project,
        "location": location,
        "realization": "agentspace_agent",
        "subtype": _agentspace_subtype(agent),
    }
    if agent.get("description"):
        props["description"] = agent["description"]
    if engine:
        props["engine"] = _leaf(engine.get("name"))

    # Engine-level grounding stores carry over to the agent; resolve ids to friendly
    # names and flag external (Microsoft) sources. NOTE: these are the Gemini Enterprise
    # app's data stores (shared across the app), not agent-owned grounding.
    knowledge: list[KnowledgeRef] = []
    lookup = data_stores or {}
    data_store_ids = (engine or {}).get("dataStoreIds") if engine else None
    for ds in data_store_ids or []:
        ds_id = str(ds)
        resource = lookup.get(ds_id, {})
        ds_name = resource.get("displayName") or ds_id
        knowledge.append(
            KnowledgeRef(
                name=ds_name,
                kind=KnowledgeKind.DISCOVERY_ENGINE_DATASTORE,
                connection_reference=ds_id,
                assignment="app",  # engine-level: shared across the Gemini Enterprise app
                external_source=_external_data_source(resource, ds_id, ds_name),
            )
        )

    scope = str((agent.get("sharingConfig") or {}).get("scope") or "").lower()
    shared = scope in ("all_users", "all", "everyone")
    state = str(agent.get("state") or "").upper()
    status = "inactive" if state in ("DISABLED", "SUSPENDED") else "active"

    # Invocation mode is recorded as a *posture* signal only. AUTOMATIC means the
    # assistant can auto-route to this agent without the user explicitly selecting
    # it, but a human is still driving the conversation — so (unlike Copilot's
    # headless external trigger) it is NOT treated as an autonomous agent and does
    # not fire SWEEP-004 / amplify SWEEP-011.
    invocation = (agent.get("agentInvocationSpec") or {}).get("invocationMode")
    if invocation:
        props["invocation_mode"] = invocation

    # Behavior is grafted from the backing runtime's no-code design config, if found.
    prov_name = _agentspace_provisioned_engine(agent)
    engine_id = _leaf(prov_name) if prov_name else None
    lowcode = (lowcode_by_engine or {}).get(engine_id) if engine_id else None
    model, model_tier, instructions, tools, observed = _lowcode_behavior(lowcode)
    if observed and lowcode is not None:
        props["behavior_source"] = {
            "kind": "agent_engine",
            "engine_id": engine_id,
            "lowcode_agent": _leaf(lowcode.get("name")),
            "name": _lowcode_display_name(lowcode),
        }
    unobservable = [f for f in ("model", "instructions", "tools") if f not in observed]
    if unobservable:
        props["unobservable"] = unobservable

    external_id = prov_name or name_path
    if not external_id:
        external_id = f"projects/{project}/locations/{location}/agents/{display}"

    return Agent(
        name=display,
        external_id=external_id,
        source_system=SourceSystem.AGENTSPACE,
        provider="google",
        kind=AgentKind.AGENT,
        model=model,
        model_tier=model_tier,
        instructions=instructions,
        tools=tools,
        knowledge=knowledge,
        status=status,
        shared_with_everyone=shared,
        created_on=agent.get("createTime"),
        modified_on=agent.get("updateTime"),
        properties=props,
    )
