"""Minimal, read-only Microsoft Dataverse Web API client for Copilot Studio.

Clean-room implementation. Flow:
  1. MSAL client-credentials auth.
  2. Discover Power Platform environments via the global discovery service.
  3. Per environment, read `bots` and `botcomponents` over OData (paginated).

Formatted-value annotations are requested so owner display names and picklist
labels come back inline — no extra lookups needed.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..errors import DiscoveryError
from .auth import TokenProvider
from .constants import (
    API_VERSION,
    BOT_SELECT,
    BOTCOMPONENT_SELECT,
    DISCOVERY_SCOPE,
    DISCOVERY_URL,
)

_PREFER = 'odata.include-annotations="OData.Community.Display.V1.FormattedValue"'
_MAX_RETRIES = 4


def _scope_for(api_url: str) -> str:
    """Derive a Dataverse resource scope from an instance ApiUrl.

    ``https://org.api.crm.dynamics.com`` -> ``https://org.crm.dynamics.com/.default``
    """
    host = urlsplit(api_url).netloc.replace(".api.", ".")
    return f"https://{host}/.default"


class DataverseClient:
    def __init__(self, auth: TokenProvider, *, timeout: float = 30.0) -> None:
        self._auth = auth
        self._http = httpx.Client(timeout=timeout)

    # ── auth ──────────────────────────────────────────────────────────────
    def _token(self, scope: str) -> str:
        return self._auth.get_token(scope)

    # ── http ──────────────────────────────────────────────────────────────
    def _get(self, url: str, scope: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._token(scope)}",
            "Accept": "application/json",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
            "Prefer": _PREFER,
        }
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self._http.get(url, headers=headers, params=params)
            except httpx.HTTPError as exc:  # network error
                raise DiscoveryError(f"request to {url} failed: {exc}") from exc
            if resp.status_code in (429, 503) and attempt < _MAX_RETRIES - 1:
                time.sleep(min(2**attempt, float(resp.headers.get("Retry-After", 2))))
                continue
            if resp.status_code == 403:
                raise DiscoveryError(
                    f"access denied ({resp.status_code}) for {url} — the app registration "
                    "may lack Copilot Studio / Dataverse read permissions"
                )
            if resp.status_code >= 400:
                raise DiscoveryError(f"{resp.status_code} from {url}: {resp.text[:200]}")
            return resp.json()
        raise DiscoveryError(f"giving up after retries: {url}")

    def _paginate(self, url: str, scope: str, params: dict[str, str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        data = self._get(url, scope, params)
        rows.extend(data.get("value", []))
        # subsequent pages carry their query in the nextLink itself
        next_link = data.get("@odata.nextLink")
        while next_link:
            data = self._get(next_link, scope)
            rows.extend(data.get("value", []))
            next_link = data.get("@odata.nextLink")
        return rows

    # ── discovery ─────────────────────────────────────────────────────────
    def list_environments(self) -> list[dict[str, str]]:
        data = self._get(DISCOVERY_URL, DISCOVERY_SCOPE)
        envs: list[dict[str, str]] = []
        for inst in data.get("value", []):
            api_url = inst.get("ApiUrl")
            if not api_url:
                continue
            envs.append(
                {
                    "unique_name": inst.get("UniqueName") or inst.get("Id") or "",
                    "friendly_name": inst.get("FriendlyName") or "",
                    "api_url": api_url,
                }
            )
        return envs

    def list_bots(self, api_url: str) -> list[dict[str, Any]]:
        scope = _scope_for(api_url)
        url = f"{api_url}/api/data/{API_VERSION}/bots"
        return self._paginate(url, scope, {"$select": BOT_SELECT})

    def list_botcomponents(self, api_url: str) -> list[dict[str, Any]]:
        scope = _scope_for(api_url)
        url = f"{api_url}/api/data/{API_VERSION}/botcomponents"
        return self._paginate(url, scope, {"$select": BOTCOMPONENT_SELECT})

    def close(self) -> None:
        self._http.close()
