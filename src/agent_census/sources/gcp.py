"""Live Google Cloud discovery source.

Fans out across ``project x location x surface``. There is NO org-wide project
auto-enumeration — coverage equals the projects you pass (or the credential's
home project). The regional surfaces (Agent Engine, Dialogflow) are scanned over
a default GA region set unless ``--location`` overrides it, so agents deployed
elsewhere are silently missed.

Degradation mirrors the other sources: a surface whose API is simply not enabled
in a region is skipped quietly (expected across many regions); a genuine access
error becomes a warning and the sweep continues.
"""

from __future__ import annotations

from ..errors import DiscoveryError
from ..findings import DEFAULT_STALE_DAYS
from ..live.auth import TokenProvider
from ..models import Agent, SweepResult
from .base import build_result


class GcpLiveSource:
    name = "gcp"

    def __init__(
        self,
        auth: TokenProvider | None,
        *,
        projects: list[str] | None = None,
        locations: list[str] | None = None,
        stale_days: int = DEFAULT_STALE_DAYS,
    ) -> None:
        if auth is None:
            raise DiscoveryError("gcp discovery requires authentication")
        self.auth = auth
        self.projects = projects or []
        self.locations = locations
        self.stale_days = stale_days

    def _resolve_projects(self) -> list[str]:
        if self.projects:
            return self.projects
        # Fall back to the credential's home project (SA key project_id / gcloud
        # config / ADC quota project). No org enumeration.
        default = getattr(self.auth, "default_project", None)
        return [default] if default else []

    def scan(self) -> SweepResult:
        from ..live.gcp import (
            AGENTSPACE_LOCATIONS,
            DEFAULT_LOCATIONS,
            DIALOGFLOW_GLOBAL,
            ApiNotEnabled,
            GcpClient,
        )
        from ..normalize import (
            gcp_agentspace_agent_to_agent,
            gcp_dialogflow_to_agent,
            gcp_reasoning_engine_to_agent,
        )

        projects = self._resolve_projects()
        if not projects:
            raise DiscoveryError(
                "no GCP project specified — pass --project (comma-separated for several). "
                "There is no org-wide auto-enumeration; coverage is limited to listed projects."
            )
        regions = list(self.locations) if self.locations else list(DEFAULT_LOCATIONS)

        agents: list[Agent] = []
        warnings: list[str] = []
        seen: set[str] = set()

        def _add(agent: Agent) -> None:
            if agent.external_id in seen:
                return
            seen.add(agent.external_id)
            agents.append(agent)

        def _list(fn, *args, label: str):
            """Run a list call, skipping not-enabled surfaces and warning on real errors."""
            try:
                return fn(*args)
            except ApiNotEnabled:
                return []  # API not enabled here — expected across many regions
            except DiscoveryError as exc:
                warnings.append(f"{label}: {exc}")
                return []

        client = GcpClient(self.auth)
        try:
            for project in projects:
                # Build a project-wide map of reasoningEngine id -> no-code design
                # config (lowcodeAgent), used to expose the real behavior of BOTH
                # Agent Engine records and the Gemini agents that wrap a runtime.
                lowcode_by_engine: dict[str, dict] = {}
                for loc in regions:
                    for la in _list(
                        client.list_lowcode_agents,
                        project,
                        loc,
                        label=f"{project}/{loc} lowcode_agents",
                    ):
                        eng = (la.get("agentEngineInstanceName") or "").rsplit("/", 1)[-1]
                        if eng:
                            lowcode_by_engine[eng] = la

                # Agentspace / Gemini Enterprise FIRST (engines -> assistants ->
                # agents over global/us/eu). Processed ahead of Agent Engine so a
                # Gemini agent that wraps a reasoningEngine stays the primary record
                # (its sharing / invocation posture wins the dedup) while still being
                # enriched with the runtime's behavior.
                for loc in AGENTSPACE_LOCATIONS:
                    # Resolve the collection's data stores so engine dataStoreIds get
                    # friendly names / types instead of raw ids.
                    data_stores: dict[str, dict] = {}
                    for s in _list(
                        client.list_data_stores,
                        project,
                        loc,
                        label=f"{project}/{loc} data_stores",
                    ):
                        sid = (s.get("name") or "").rsplit("/", 1)[-1]
                        if sid:
                            data_stores[sid] = s
                    for eng in _list(
                        client.list_agentspace_engines,
                        project,
                        loc,
                        label=f"{project}/{loc} agentspace",
                    ):
                        eng_name = eng.get("name") or ""
                        if not eng_name:
                            continue
                        for asst in _list(
                            client.list_agentspace_assistants,
                            eng_name,
                            loc,
                            project,
                            label=f"{project}/{loc} assistants",
                        ):
                            asst_name = asst.get("name") or ""
                            if not asst_name:
                                continue
                            for ag in _list(
                                client.list_agentspace_agents,
                                asst_name,
                                loc,
                                project,
                                label=f"{project}/{loc} agents",
                            ):
                                _add(
                                    gcp_agentspace_agent_to_agent(
                                        ag,
                                        project=project,
                                        location=loc,
                                        engine=eng,
                                        lowcode_by_engine=lowcode_by_engine,
                                        data_stores=data_stores,
                                    )
                                )

                # Agent Engine — regional only; enriched from its lowcodeAgent.
                for loc in regions:
                    for raw in _list(
                        client.list_reasoning_engines,
                        project,
                        loc,
                        label=f"{project}/{loc} agent_engine",
                    ):
                        eng_id = (raw.get("name") or "").rsplit("/", 1)[-1]
                        _add(
                            gcp_reasoning_engine_to_agent(
                                raw,
                                project=project,
                                location=loc,
                                lowcode=lowcode_by_engine.get(eng_id),
                            )
                        )

                # Dialogflow CX — regional plus the special "global" location.
                for loc in [*regions, DIALOGFLOW_GLOBAL]:
                    for ra in _list(
                        client.list_dialogflow_agents,
                        project,
                        loc,
                        label=f"{project}/{loc} dialogflow",
                    ):
                        resource = ra.get("name") or ""
                        playbooks = (
                            _list(
                                client.list_dialogflow_playbooks,
                                resource,
                                loc,
                                project,
                                label=f"{project}/{loc} playbooks",
                            )
                            if resource
                            else []
                        )
                        tools = (
                            _list(
                                client.list_dialogflow_tools,
                                resource,
                                loc,
                                project,
                                label=f"{project}/{loc} tools",
                            )
                            if resource
                            else []
                        )
                        _add(
                            gcp_dialogflow_to_agent(
                                ra, playbooks, tools, project=project, location=loc
                            )
                        )
        finally:
            client.close()

        if not agents and not warnings:
            warnings.append(
                "zero GCP agents discovered — coverage is limited to the listed projects and "
                "default regions, and the identity may lack the required Viewer roles"
            )

        env_label = ", ".join(projects) if len(projects) <= 3 else f"{len(projects)} projects"
        return build_result(
            agents,
            source="gcp",
            environment=env_label,
            warnings=warnings,
            stale_days=self.stale_days,
        )
