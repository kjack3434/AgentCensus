"""Governance / risk findings.

Pure predicates over the flat :class:`~agent_census.models.Agent` record. Each
rule has a fresh ``SWEEP-###`` id (deliberately independent of any internal rule
catalogue). ``evaluate`` runs them all; ``categorize`` buckets an agent for the
report; ``build_summary`` rolls everything up for the dashboard.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from .models import SEVERITY_RANK, Agent, Finding, Severity, Summary

DEFAULT_STALE_DAYS = 90

# Channels considered "external / consumer" reach (matched case-insensitively
# against the human-readable channel names stored on the agent).
EXTERNAL_CHANNELS = {
    "slack",
    "facebook",
    "facebook messenger",
    "telegram",
    "twilio sms",
    "sms",
    "line",
    "kik",
    "groupme",
    "email",
}

# Stock / placeholder instruction text that signals an unconfigured agent.
PLACEHOLDER_INSTRUCTIONS = {
    "",
    "you are a helpful assistant.",
    "you are a helpful assistant",
    "you are an ai assistant.",
    "enter your instructions here",
    "todo",
}


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _has_external_channel(agent: Agent) -> bool:
    return any(c.strip().lower() in EXTERNAL_CHANNELS for c in agent.channels)


# ── Rules ────────────────────────────────────────────────────────────────────
# Each rule: (agent, now, stale_days) -> Finding | None


def _rule_public_unauthenticated(agent: Agent, now: datetime, stale_days: int) -> Finding | None:
    if agent.no_auth_required and (agent.shared_with_everyone or agent.multi_tenant):
        return Finding(
            rule_id="SWEEP-001",
            title="Public, unauthenticated agent",
            severity=Severity.CRITICAL,
            message="Agent requires no authentication and is shared broadly — anyone can use it.",
            remediation="Require authentication and restrict access to specific users or groups.",
        )
    return None


# NOTE: "missing owner" and "no content guardrails" are intentionally NOT findings.
# Owners usually can't be harvested via discovery, and content-safety/RAI is applied by
# default (Copilot Studio moderates all generative calls; Foundry models/agents carry RAI
# policies) and its true posture is only visible via Microsoft Purview (DSPM for AI) /
# Azure AI Content Safety — not the discovery APIs. Flagging their absence is a false positive.


def _rule_autonomous_no_human(agent: Agent, now: datetime, stale_days: int) -> Finding | None:
    if agent.autonomous and not agent.properties.get("has_human_in_the_loop"):
        return Finding(
            rule_id="SWEEP-004",
            title="Autonomous agent without human-in-the-loop",
            severity=Severity.HIGH,
            message="Agent can be triggered autonomously with no human approval step detected.",
            remediation="Add a human approval / review step for autonomous actions.",
        )
    return None


def _rule_write_tool_no_approval(agent: Agent, now: datetime, stale_days: int) -> Finding | None:
    risky = [
        t for t in agent.tools if t.write_capable and t.requires_approval in ("never", "unknown")
    ]
    if risky:
        names = ", ".join(sorted({t.name for t in risky}))
        return Finding(
            rule_id="SWEEP-005",
            title="Write-capable tool without approval",
            severity=Severity.HIGH,
            message=f"Tool(s) can make changes without an approval gate: {names}.",
            remediation="Require approval for side-effecting tools or scope them to read-only.",
        )
    return None


def _rule_external_mcp(agent: Agent, now: datetime, stale_days: int) -> Finding | None:
    mcp = [t for t in agent.tools if t.tool_type.value == "mcp"]
    if mcp:
        names = ", ".join(sorted({t.name for t in mcp}))
        return Finding(
            rule_id="SWEEP-006",
            title="Uses external MCP server",
            severity=Severity.MEDIUM,
            message=f"Agent depends on external MCP server tool(s): {names}.",
            remediation="Review the MCP server's trust, scopes, and data handling.",
        )
    return None


def _rule_ungoverned_model(agent: Agent, now: datetime, stale_days: int) -> Finding | None:
    tier = (agent.model_tier or "").lower()
    model = (agent.model or "").strip().lower()
    if tier in ("experimental", "preview") or model in ("", "unknown"):
        detail = (
            f"tier '{agent.model_tier}'"
            if tier in ("experimental", "preview")
            else "an unknown model"
        )
        return Finding(
            rule_id="SWEEP-007",
            title="Experimental, preview, or unknown model",
            severity=Severity.MEDIUM,
            message=f"Agent runs on {detail}; governance and stability guarantees may not apply.",
            remediation="Pin the agent to a governed, generally-available model.",
        )
    return None


def _rule_broad_channels(agent: Agent, now: datetime, stale_days: int) -> Finding | None:
    if len(agent.channels) >= 3 or (agent.shared_with_everyone and _has_external_channel(agent)):
        chans = ", ".join(agent.channels) or "multiple channels"
        return Finding(
            rule_id="SWEEP-008",
            title="Broad channel exposure",
            severity=Severity.MEDIUM,
            message=f"Agent is published to a wide surface: {chans}.",
            remediation="Limit the agent to the channels it actually needs.",
        )
    return None


def _rule_empty_instructions(agent: Agent, now: datetime, stale_days: int) -> Finding | None:
    placeholder = agent.instructions.strip().lower() in PLACEHOLDER_INSTRUCTIONS
    if agent.instructions_length == 0 or placeholder:
        return Finding(
            rule_id="SWEEP-009",
            title="Empty or placeholder instructions",
            severity=Severity.LOW,
            message=(
                "Agent has empty or boilerplate instructions — it may be unconfigured or abandoned."
            ),
            remediation="Add purpose-specific instructions or retire the agent.",
        )
    return None


def _rule_stale(agent: Agent, now: datetime, stale_days: int) -> Finding | None:
    if agent.modified_on is None:
        return None
    age = now - _as_utc(agent.modified_on)
    if age.days > stale_days:
        return Finding(
            rule_id="SWEEP-010",
            title="Stale agent",
            severity=Severity.LOW,
            message=f"Not modified in {age.days} days (threshold {stale_days}).",
            remediation="Confirm the agent is still needed; retire it if not.",
        )
    return None


_RULES = (
    _rule_public_unauthenticated,
    _rule_autonomous_no_human,
    _rule_write_tool_no_approval,
    _rule_external_mcp,
    _rule_ungoverned_model,
    _rule_broad_channels,
    _rule_empty_instructions,
    _rule_stale,
)


def evaluate(
    agent: Agent,
    *,
    now: datetime | None = None,
    stale_days: int = DEFAULT_STALE_DAYS,
) -> list[Finding]:
    """Run all rules against an agent and return the findings (most severe first)."""
    now = now or datetime.now(UTC)
    found = [f for rule in _RULES if (f := rule(agent, now, stale_days)) is not None]
    found.sort(key=lambda f: SEVERITY_RANK[f.severity], reverse=True)
    return found


def categorize(agent: Agent) -> str:
    """Bucket an agent for the report's category chip."""
    if agent.autonomous:
        return "autonomous"
    if agent.shared_with_everyone or agent.multi_tenant or _has_external_channel(agent):
        return "customer_facing"
    return "internal"


def annotate(
    agent: Agent,
    *,
    now: datetime | None = None,
    stale_days: int = DEFAULT_STALE_DAYS,
) -> Agent:
    """Compute and attach findings + category to an agent in place."""
    agent.findings = evaluate(agent, now=now, stale_days=stale_days)
    agent.category = categorize(agent)
    return agent


def build_summary(agents: list[Agent]) -> Summary:
    """Roll up source / model / status / category counts and a severity tally."""
    by_source: Counter[str] = Counter()
    by_model: Counter[str] = Counter()
    by_status: Counter[str] = Counter()
    by_category: Counter[str] = Counter()
    by_severity: Counter[str] = Counter()
    total_findings = 0

    for a in agents:
        by_source[a.source_system.value] += 1
        by_model[a.model or "unknown"] += 1
        by_status[a.status or "unknown"] += 1
        by_category[a.category or "uncategorized"] += 1
        for f in a.findings:
            by_severity[f.severity.value] += 1
            total_findings += 1

    return Summary(
        total_agents=len(agents),
        total_findings=total_findings,
        by_source=dict(by_source),
        by_model=dict(by_model),
        by_status=dict(by_status),
        by_category=dict(by_category),
        findings_by_severity=dict(by_severity),
    )
