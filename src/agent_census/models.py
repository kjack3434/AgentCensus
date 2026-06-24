"""Core data model for agentcensus.

A single flat, JSON-serializable ``Agent`` record normalizes agents from any
source (Copilot Studio bots, Azure AI Foundry agents) into one shape. The whole
discovery result serializes through one path (``model_dump(mode="json")``) so the
HTML report and the ``--format json`` output can never drift.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "agentcensus/v1"


class SourceSystem(StrEnum):
    """Where an agent was discovered."""

    COPILOT_STUDIO = "copilot_studio"
    AZURE_AI_FOUNDRY = "azure_ai_foundry"
    # Google Cloud surfaces (kept per-surface so coverage rolls up honestly).
    VERTEX_AI_AGENT_ENGINE = "vertex_ai_agent_engine"
    AGENTSPACE = "agentspace"
    DIALOGFLOW_CX = "dialogflow_cx"


class AgentKind(StrEnum):
    """Realization kind of the agent."""

    PROMPT = "prompt"
    HOSTED = "hosted"
    WORKFLOW = "workflow"
    DECLARATIVE = "declarative"  # Copilot Studio GPT-template ("Custom GPT") bot
    CUSTOM = "custom"  # Copilot Studio classic / custom bot
    AGENT = "agent"  # generic fallback


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


SEVERITY_RANK: dict[Severity, int] = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}

# Severity values ordered from most to least severe — handy for stable display.
SEVERITY_ORDER: list[str] = [
    s.value
    for s in (
        Severity.CRITICAL,
        Severity.HIGH,
        Severity.MEDIUM,
        Severity.LOW,
        Severity.INFO,
    )
]


class ToolType(StrEnum):
    FUNCTION = "function"
    CODE_INTERPRETER = "code_interpreter"
    FILE_SEARCH = "file_search"
    OPENAPI = "openapi"
    MCP = "mcp"
    WEB_BROWSE = "web_browse"
    HTTP = "http"
    CONNECTOR_ACTION = "connector_action"
    SKILL = "skill"
    OTHER = "other"


class KnowledgeKind(StrEnum):
    AZURE_SEARCH = "azure_search"
    SHAREPOINT = "sharepoint"
    DATAVERSE_TABLE = "dataverse_table"
    FILE_UPLOAD = "file_upload"
    WEB = "web"
    # Google Cloud grounding stores.
    VERTEX_AI_SEARCH = "vertex_ai_search"
    DISCOVERY_ENGINE_DATASTORE = "discovery_engine_datastore"
    OTHER = "other"


class _Model(BaseModel):
    # protected_namespaces=() is load-bearing: ``Agent`` has a field named ``model``
    # and Pydantic would otherwise warn about the ``model_`` namespace clash.
    model_config = ConfigDict(protected_namespaces=())


class ToolRef(_Model):
    name: str
    tool_type: ToolType = ToolType.OTHER
    server_url: str | None = None
    # never | always | conditional | unknown
    requires_approval: str = "unknown"
    write_capable: bool = False
    source: str | None = None


class KnowledgeRef(_Model):
    name: str
    kind: KnowledgeKind = KnowledgeKind.OTHER
    scope: str | None = None
    index_name: str | None = None
    connection_reference: str | None = None
    # "agent" = bound to this agent (Copilot/Foundry); "app" = shared at the app
    # level (e.g. a Gemini Enterprise engine data store, not agent-owned).
    assignment: str = "agent"
    # Name of the external / cross-trust-boundary system this grounds on (e.g.
    # "sharepoint", "web"), or None if the source is native to the agent's platform.
    external_source: str | None = None


class GuardrailRef(_Model):
    kind: str
    level: str | None = None
    source: str | None = None


class OwnerRef(_Model):
    name: str | None = None
    email: str | None = None
    oid: str | None = None
    source: str | None = None


class Finding(_Model):
    rule_id: str
    title: str
    severity: Severity
    message: str = ""
    remediation: str | None = None


class Agent(_Model):
    # identity
    name: str
    external_id: str
    source_system: SourceSystem
    provider: str = "microsoft"

    # classification / capability
    kind: AgentKind = AgentKind.AGENT
    model: str | None = None
    model_tier: str | None = None  # standard | experimental | preview | platform_default
    instructions: str = ""
    instructions_length: int = 0  # derived from instructions

    # embedded relationships (inlined rather than separate edge objects)
    tools: list[ToolRef] = Field(default_factory=list)
    knowledge: list[KnowledgeRef] = Field(default_factory=list)
    guardrails: list[GuardrailRef] = Field(default_factory=list)
    owners: list[OwnerRef] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)

    # posture
    status: str = "active"
    version: str | None = None
    autonomous: bool = False
    shared_with_everyone: bool = False
    no_auth_required: bool = False
    multi_tenant: bool = False

    # lifecycle
    created_on: datetime | None = None
    modified_on: datetime | None = None
    published_on: datetime | None = None

    # escape hatch for source-specific signals + computed fields
    properties: dict[str, Any] = Field(default_factory=dict)
    findings: list[Finding] = Field(default_factory=list)
    category: str | None = None

    def model_post_init(self, _context: Any) -> None:
        # instructions_length is always derived so callers/fixtures can't desync it.
        object.__setattr__(self, "instructions_length", len(self.instructions or ""))

    @property
    def max_severity_rank(self) -> int:
        """Rank of this agent's worst finding (-1 if clean)."""
        return max((SEVERITY_RANK[f.severity] for f in self.findings), default=-1)


class Summary(_Model):
    total_agents: int = 0
    total_findings: int = 0
    by_source: dict[str, int] = Field(default_factory=dict)
    by_model: dict[str, int] = Field(default_factory=dict)
    by_status: dict[str, int] = Field(default_factory=dict)
    by_category: dict[str, int] = Field(default_factory=dict)
    findings_by_severity: dict[str, int] = Field(default_factory=dict)


class SweepMeta(_Model):
    generated_at: datetime
    source: str
    tool_version: str
    environment: str | None = None


class SweepResult(_Model):
    schema_version: str = SCHEMA_VERSION
    meta: SweepMeta
    summary: Summary = Field(default_factory=Summary)
    agents: list[Agent] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
