"""Live Azure AI Foundry discovery source."""

from __future__ import annotations

from ..errors import DiscoveryError
from ..findings import DEFAULT_STALE_DAYS
from ..live.auth import TokenProvider
from ..models import SweepResult
from ..normalize import foundry_def_to_agent
from .base import build_result


class FoundryLiveSource:
    name = "foundry"

    def __init__(
        self,
        auth: TokenProvider | None,
        *,
        subscription: str | None = None,
        stale_days: int = DEFAULT_STALE_DAYS,
    ) -> None:
        if auth is None:
            raise DiscoveryError("foundry discovery requires authentication")
        self.auth = auth
        self.subscription = subscription
        self.stale_days = stale_days

    def scan(self) -> SweepResult:
        from ..live.foundry import FoundryClient, derive_project_endpoint

        client = FoundryClient(self.auth)
        agents = []
        warnings: list[str] = []
        subs_scanned = 0
        projects_seen = 0
        try:
            subs = client.list_subscriptions()
            if self.subscription:
                subs = [s for s in subs if s == self.subscription]
                if not subs:
                    raise DiscoveryError(
                        f"subscription {self.subscription!r} not found or inaccessible"
                    )

            for sub in subs:
                try:
                    accounts = client.list_ai_accounts(sub)
                except DiscoveryError as exc:
                    warnings.append(f"subscription {sub}: {exc}")
                    continue
                subs_scanned += 1

                for account in accounts:
                    try:
                        projects = client.list_projects(account["id"])
                    except DiscoveryError as exc:
                        warnings.append(f"account {account.get('name')}: {exc}")
                        continue

                    for project in projects:
                        projects_seen += 1
                        project_name = str(project.get("name", "")).split("/")[-1]
                        endpoint = derive_project_endpoint(account, project_name)
                        project_ext = project.get("id") or f"{account.get('name')}/{project_name}"
                        try:
                            raw_agents = client.list_agents(endpoint)
                        except DiscoveryError as exc:
                            warnings.append(f"project {project_name}: {exc}")
                            continue
                        agents.extend(
                            foundry_def_to_agent(raw, project_external_id=project_ext)
                            for raw in raw_agents
                        )
        finally:
            client.close()

        if not agents and subs_scanned:
            warnings.append(
                "zero Foundry agents discovered — the signed-in identity may lack access, "
                "or no agents exist in the visible projects"
            )

        if self.subscription:
            env_label: str | None = f"subscription {self.subscription}"
        elif projects_seen:
            env_label = f"{projects_seen} projects"
        else:
            env_label = f"{subs_scanned} subscriptions"

        return build_result(
            agents,
            source="foundry",
            environment=env_label,
            warnings=warnings,
            stale_days=self.stale_days,
        )
