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
            knowledge.append(
                KnowledgeRef(
                    name=comp.get("name") or comp.get("schemaname") or "knowledge",
                    kind=_knowledge_kind(data),
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
