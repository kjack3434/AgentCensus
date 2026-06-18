"""Minimal, read-only Azure AI Foundry client.

Clean-room implementation over public Azure REST APIs. Flow:
  1. ARM: list subscriptions -> AIServices accounts -> projects.
  2. Derive each project's data-plane endpoint from the account's endpoints.
  3. Data-plane: list agents for the project.

Two pagination dialects: ARM (`value` + `nextLink`) and the data-plane / OpenAI
convention (`data` + `has_more` + `last_id` -> `after=` cursor).
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

import httpx

from ..errors import DiscoveryError
from .auth import TokenProvider

ARM_BASE = "https://management.azure.com"
ARM_SCOPE = "https://management.azure.com/.default"
DATA_SCOPE = "https://ai.azure.com/.default"

API_SUBSCRIPTIONS = "2022-12-01"
API_ACCOUNTS = "2025-06-01"
API_PROJECTS = "2025-06-01"
API_AGENTS = "v1"

_AISERVICES_KINDS = {"aiservices", "aiservice"}
_MAX_RETRIES = 4


_FOUNDRY_HOST = "services.ai.azure.com"


def _is_foundry_endpoint(val: Any) -> bool:
    """True if ``val`` is a URL whose *host* is (a subdomain of) the Foundry data plane.

    Matches on the parsed hostname rather than a substring, so a spoofed host such as
    ``services.ai.azure.com.evil.example`` is rejected.
    """
    if not isinstance(val, str):
        return False
    host = (urlparse(val).hostname or "").lower()
    return host == _FOUNDRY_HOST or host.endswith("." + _FOUNDRY_HOST)


def derive_project_endpoint(account: dict[str, Any], project_name: str) -> str:
    """Build the data-plane project endpoint from an account's endpoint map."""
    endpoints = (account.get("properties") or {}).get("endpoints") or {}
    base = endpoints.get("AI Foundry API") or endpoints.get("AIFoundry") or endpoints.get("Foundry")
    if not base:
        for key, val in endpoints.items():
            if "foundry" in key.lower() and _is_foundry_endpoint(val):
                base = val
                break
    if not base:
        base = f"https://{account.get('name')}.{_FOUNDRY_HOST}"
    return f"{base.rstrip('/')}/api/projects/{project_name}"


class FoundryClient:
    def __init__(self, auth: TokenProvider, *, timeout: float = 30.0) -> None:
        self._auth = auth
        self._http = httpx.Client(timeout=timeout)

    def _get(self, url: str, scope: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._auth.get_token(scope)}",
            "Accept": "application/json",
        }
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self._http.get(url, headers=headers, params=params)
            except httpx.HTTPError as exc:
                raise DiscoveryError(f"request to {url} failed: {exc}") from exc
            if resp.status_code in (429, 503) and attempt < _MAX_RETRIES - 1:
                time.sleep(min(2**attempt, float(resp.headers.get("Retry-After", 2))))
                continue
            if resp.status_code in (401, 403):
                raise DiscoveryError(
                    f"access denied ({resp.status_code}) for {url} — the signed-in identity "
                    "may lack Azure AI Foundry read access"
                )
            if resp.status_code >= 400:
                raise DiscoveryError(f"{resp.status_code} from {url}: {resp.text[:200]}")
            return resp.json()
        raise DiscoveryError(f"giving up after retries: {url}")

    def _paginate_arm(self, url: str, params: dict[str, str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        data = self._get(url, ARM_SCOPE, params)
        rows.extend(data.get("value", []))
        next_link = data.get("nextLink")
        while next_link:
            data = self._get(next_link, ARM_SCOPE)
            rows.extend(data.get("value", []))
            next_link = data.get("nextLink")
        return rows

    def _paginate_data(self, url: str, params: dict[str, str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page = dict(params)
        while True:
            data = self._get(url, DATA_SCOPE, page)
            rows.extend(data.get("data", []))
            if not data.get("has_more") or not data.get("last_id"):
                break
            page = {**params, "after": data["last_id"]}
        return rows

    # ── control plane ──────────────────────────────────────────────────────
    def list_subscriptions(self) -> list[str]:
        rows = self._paginate_arm(f"{ARM_BASE}/subscriptions", {"api-version": API_SUBSCRIPTIONS})
        return [r["subscriptionId"] for r in rows if r.get("subscriptionId")]

    def list_ai_accounts(self, subscription_id: str) -> list[dict[str, Any]]:
        url = (
            f"{ARM_BASE}/subscriptions/{subscription_id}"
            "/providers/Microsoft.CognitiveServices/accounts"
        )
        rows = self._paginate_arm(url, {"api-version": API_ACCOUNTS})
        return [r for r in rows if str(r.get("kind", "")).lower() in _AISERVICES_KINDS]

    def list_projects(self, account_id: str) -> list[dict[str, Any]]:
        url = f"{ARM_BASE}{account_id}/projects"
        return self._paginate_arm(url, {"api-version": API_PROJECTS})

    # ── data plane ───────────────────────────────────────────────────────────
    def list_agents(self, endpoint: str) -> list[dict[str, Any]]:
        return self._paginate_data(f"{endpoint}/agents", {"api-version": API_AGENTS})

    def close(self) -> None:
        self._http.close()
