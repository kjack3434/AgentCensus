"""Live Microsoft Copilot Studio discovery source."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..errors import DiscoveryError
from ..findings import DEFAULT_STALE_DAYS
from ..live.auth import TokenProvider
from ..models import SweepResult
from ..normalize import copilot_bot_to_agent
from .base import build_result


class CopilotStudioLiveSource:
    name = "copilot_studio"

    def __init__(
        self,
        auth: TokenProvider | None,
        *,
        environment: str | None = None,
        stale_days: int = DEFAULT_STALE_DAYS,
    ) -> None:
        if auth is None:
            raise DiscoveryError("copilot-studio discovery requires authentication")
        self.auth = auth
        self.environment = environment
        self.stale_days = stale_days

    def _select_environments(self, envs: list[dict[str, str]]) -> list[dict[str, str]]:
        """Filter environments by --environment (unique/friendly name, or api_url substring)."""
        if not self.environment:
            return envs
        wanted = self.environment.lower()
        picked = [
            e
            for e in envs
            if wanted in (e.get("unique_name", "").lower(), e.get("friendly_name", "").lower())
            or wanted in e.get("api_url", "").lower()
        ]
        return picked

    def scan(self) -> SweepResult:
        from ..live.dataverse import DataverseClient

        client = DataverseClient(self.auth)
        agents = []
        warnings: list[str] = []
        envs_scanned = 0
        try:
            envs = client.list_environments()
            if self.environment:
                envs = self._select_environments(envs)
                if not envs:
                    raise DiscoveryError(f"no environment matched {self.environment!r}")

            for env in envs:
                api_url = env["api_url"]
                try:
                    bots = client.list_bots(api_url)
                    components = client.list_botcomponents(api_url)
                except DiscoveryError as exc:
                    warnings.append(f"{env.get('friendly_name') or api_url}: {exc}")
                    continue

                envs_scanned += 1
                by_parent: dict[Any, list[dict[str, Any]]] = defaultdict(list)
                for comp in components:
                    parent = comp.get("_parentbotid_value")
                    if parent:
                        by_parent[parent].append(comp)

                org = env.get("unique_name") or env.get("friendly_name")
                for bot in bots:
                    comps = by_parent.get(bot.get("botid"), [])
                    agents.append(copilot_bot_to_agent(bot, comps, org=org))
        finally:
            client.close()

        if not agents and envs_scanned:
            warnings.append(
                "zero agents discovered — the app registration may lack Copilot Studio read access"
            )

        if self.environment:
            env_label: str | None = self.environment
        elif envs_scanned == 1:
            env_label = envs[0].get("friendly_name") or envs[0].get("unique_name")
        else:
            env_label = f"{envs_scanned} environments"

        return build_result(
            agents,
            source="copilot_studio",
            environment=env_label,
            warnings=warnings,
            stale_days=self.stale_days,
        )
