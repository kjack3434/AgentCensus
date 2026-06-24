"""Minimal, read-only Google Cloud agent-discovery client.

Clean-room implementation over the public Google Cloud REST APIs. Three agent
surfaces:

  * Vertex AI **Agent Engine**  — ``reasoningEngines`` (aiplatform, v1beta1),
    enumerated per project x region.
  * **Dialogflow CX**           — ``agents`` + per-agent ``playbooks`` / ``tools``
    (v3), per project x region (+ ``global``).
  * **Agentspace / Gemini Enterprise** — Discovery Engine (v1alpha), per project x
    {global, us, eu}: ``engines`` -> ``assistants`` -> ``agents`` (the built-in
    "Deep Research" / "Idea Generation" and custom agents live at the agent level).

GCP uses one broad ``cloud-platform`` audience, so a single token is reused
across every call. **User credentials** (gcloud / ADC) additionally send an
``x-goog-user-project`` header so Discovery Engine / Dialogflow attribute quota
to the scanned project — without it the call is billed to a shared project and
returns a misleading ``SERVICE_DISABLED`` 403.

A surface whose API is genuinely not enabled (HTTP 404, or 403 with
``error.details[].reason == "SERVICE_DISABLED"`` and no quota-project hint) is
*benign*: it raises :class:`ApiNotEnabled` so the caller skips it with a warning
rather than aborting. A real 401 / 403-PERMISSION_DENIED / quota-project / 5xx is
a genuine :class:`DiscoveryError`.

Endpoints and API versions are pinned here (the Agentspace v1alpha / Agent
Engine v1beta1 surfaces are pre-GA and may churn — isolate the change to this
module).
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from ..errors import DiscoveryError
from .auth import GCP_CLOUD_PLATFORM_SCOPE, TokenProvider

# Pinned API versions (pre-GA surfaces — expect churn).
API_AIPLATFORM = "v1beta1"
API_DIALOGFLOW = "v3"
API_DISCOVERYENGINE = "v1alpha"

# Default General-Availability regions to fan out over for the regional surfaces
# (Agent Engine, Dialogflow). NOT an org enumeration — agents deployed in other
# regions are silently missed unless --location overrides this set.
DEFAULT_LOCATIONS = (
    "us-central1",
    "us-east4",
    "us-west1",
    "europe-west1",
    "europe-west4",
    "asia-east1",
    "asia-northeast1",
    "asia-southeast1",
)

# Dialogflow CX also keeps agents under the special "global" location.
DIALOGFLOW_GLOBAL = "global"
# Discovery Engine / Agentspace uses multi-region locations, NOT GCP regions.
AGENTSPACE_LOCATIONS = ("global", "us", "eu")

# Scratch engines the Agent Designer creates are intentionally never inventoried.
_SCRATCH_MARKER = "AGENT_DESIGNER_GENERATED_DO_NOT_DELETE"

_MAX_RETRIES = 4


class ApiNotEnabled(DiscoveryError):
    """A surface's API is disabled / unavailable here — skip it, don't abort."""


def _aiplatform_host(location: str) -> str:
    # Agent Engine is regional; there is no global host.
    return f"https://{location}-aiplatform.googleapis.com"


def _dialogflow_host(location: str) -> str:
    if location == DIALOGFLOW_GLOBAL:
        return "https://dialogflow.googleapis.com"
    return f"https://{location}-dialogflow.googleapis.com"


def _discoveryengine_host(location: str) -> str:
    if location in ("global", ""):
        return "https://discoveryengine.googleapis.com"
    return f"https://{location}-discoveryengine.googleapis.com"


def _retry_delay(attempt: int, retry_after: str | None) -> float:
    """Backoff seconds for a 429/503: honor a numeric ``Retry-After``, capped by
    exponential backoff. ``Retry-After`` may be an HTTP-date (RFC-7231) — we don't
    parse dates; we fall back to backoff rather than crash on ``float()``."""
    seconds = 2.0
    if retry_after:
        try:
            seconds = float(retry_after)
        except ValueError:
            seconds = 2.0
    return min(float(2**attempt), seconds)


def _is_service_disabled(body: dict[str, Any]) -> bool:
    """True if a 403 body indicates the API is not enabled (vs a real denial).

    A *quota project* 403 also carries ``reason == "SERVICE_DISABLED"`` but the
    real cause is a missing ``x-goog-user-project`` header (the request was billed
    to a shared project). That is NOT a benign "API disabled" — treat it as a real
    error so it surfaces as a warning instead of silently dropping the surface.
    """
    err = body.get("error") or {}
    msg = str(err.get("message") or "").lower()
    if "quota project" in msg:
        return False
    for detail in err.get("details") or []:
        if isinstance(detail, dict) and detail.get("reason") == "SERVICE_DISABLED":
            return True
    return "has not been used" in msg or "is disabled" in msg or "service_disabled" in msg


class GcpClient:
    def __init__(self, auth: TokenProvider, *, timeout: float = 30.0) -> None:
        self._auth = auth
        self._http = httpx.Client(timeout=timeout)
        # User creds (gcloud/ADC) must attribute quota to the scanned project.
        self._send_quota = bool(getattr(auth, "needs_quota_project", False))

    def _get(
        self, url: str, params: dict[str, str] | None = None, *, quota_project: str | None = None
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._auth.get_token(GCP_CLOUD_PLATFORM_SCOPE)}",
            "Accept": "application/json",
        }
        if self._send_quota and quota_project:
            headers["x-goog-user-project"] = quota_project
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self._http.get(url, headers=headers, params=params)
            except httpx.HTTPError as exc:
                raise DiscoveryError(f"request to {url} failed: {exc}") from exc

            if resp.status_code in (429, 503) and attempt < _MAX_RETRIES - 1:
                time.sleep(_retry_delay(attempt, resp.headers.get("Retry-After")))
                continue

            if resp.status_code == 404:
                raise ApiNotEnabled(f"not available here (404): {url}")
            if resp.status_code == 403:
                body = self._safe_json(resp)
                if _is_service_disabled(body):
                    raise ApiNotEnabled(f"API not enabled: {url}")
                raise DiscoveryError(
                    f"access denied (403) for {url} — the identity may lack the "
                    "required IAM Viewer role"
                )
            if resp.status_code == 401:
                raise DiscoveryError(f"authentication failed (401) for {url}")
            if resp.status_code >= 400:
                raise DiscoveryError(f"{resp.status_code} from {url}: {resp.text[:200]}")
            try:
                return resp.json()
            except ValueError as exc:  # e.g. an HTML page where a 200 JSON was expected
                raise DiscoveryError(f"non-JSON response from {url}") from exc
        raise DiscoveryError(f"giving up after retries: {url}")

    @staticmethod
    def _safe_json(resp: httpx.Response) -> dict[str, Any]:
        try:
            data = resp.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def _paginate(
        self,
        url: str,
        items_key: str,
        params: dict[str, str] | None = None,
        *,
        quota_project: str | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page = dict(params or {})
        while True:
            data = self._get(url, page, quota_project=quota_project)
            rows.extend(x for x in data.get(items_key, []) if isinstance(x, dict))
            token = data.get("nextPageToken")
            if not token:
                break
            page = {**(params or {}), "pageToken": token}
        return rows

    # ── Vertex AI Agent Engine (reasoningEngines) ──────────────────────────────
    def list_reasoning_engines(self, project: str, location: str) -> list[dict[str, Any]]:
        url = (
            f"{_aiplatform_host(location)}/{API_AIPLATFORM}"
            f"/projects/{project}/locations/{location}/reasoningEngines"
        )
        engines = self._paginate(url, "reasoningEngines", quota_project=project)
        # Drop Agent-Designer scratch engines — not real inventory.
        return [e for e in engines if not _is_scratch(e)]

    def list_lowcode_agents(self, project: str, location: str) -> list[dict[str, Any]]:
        """No-code Agent Designer agents — the *design config* (instruction / model /
        tools) behind a reasoningEngine runtime.

        Served only on the console ``/ui/`` surface (no public v1/v1beta1 entry), but
        reachable with a normal token. Best-effort: a region without it 404s and is
        skipped, so this only ever *enriches* the Agent Engine records.
        """
        url = (
            f"{_aiplatform_host(location)}/ui/projects/{project}/locations/{location}/lowcodeAgents"
        )
        return self._paginate(url, "lowcodeAgents", quota_project=project)

    # ── Dialogflow CX (agents -> playbooks / tools) ────────────────────────────
    def list_dialogflow_agents(self, project: str, location: str) -> list[dict[str, Any]]:
        url = (
            f"{_dialogflow_host(location)}/{API_DIALOGFLOW}"
            f"/projects/{project}/locations/{location}/agents"
        )
        return self._paginate(url, "agents", quota_project=project)

    def list_dialogflow_playbooks(
        self, agent_resource: str, location: str, project: str
    ) -> list[dict[str, Any]]:
        url = f"{_dialogflow_host(location)}/{API_DIALOGFLOW}/{agent_resource}/playbooks"
        return self._paginate(url, "playbooks", quota_project=project)

    def list_dialogflow_tools(
        self, agent_resource: str, location: str, project: str
    ) -> list[dict[str, Any]]:
        url = f"{_dialogflow_host(location)}/{API_DIALOGFLOW}/{agent_resource}/tools"
        return self._paginate(url, "tools", quota_project=project)

    # ── Agentspace / Gemini Enterprise (engines -> assistants -> agents) ───────
    def list_agentspace_engines(self, project: str, location: str) -> list[dict[str, Any]]:
        url = (
            f"{_discoveryengine_host(location)}/{API_DISCOVERYENGINE}"
            f"/projects/{project}/locations/{location}"
            "/collections/default_collection/engines"
        )
        return self._paginate(url, "engines", quota_project=project)

    def list_agentspace_assistants(
        self, engine_resource: str, location: str, project: str
    ) -> list[dict[str, Any]]:
        host = _discoveryengine_host(location)
        url = f"{host}/{API_DISCOVERYENGINE}/{engine_resource}/assistants"
        return self._paginate(url, "assistants", quota_project=project)

    def list_agentspace_agents(
        self, assistant_resource: str, location: str, project: str
    ) -> list[dict[str, Any]]:
        host = _discoveryengine_host(location)
        url = f"{host}/{API_DISCOVERYENGINE}/{assistant_resource}/agents"
        return self._paginate(url, "agents", quota_project=project)

    def list_data_stores(self, project: str, location: str) -> list[dict[str, Any]]:
        """Collection-level data stores — used to resolve engine ``dataStoreIds`` to
        friendly names / types (the engine only returns bare ids)."""
        host = _discoveryengine_host(location)
        url = (
            f"{host}/{API_DISCOVERYENGINE}/projects/{project}/locations/{location}"
            "/collections/default_collection/dataStores"
        )
        return self._paginate(url, "dataStores", quota_project=project)

    def close(self) -> None:
        self._http.close()


def _is_scratch(engine: dict[str, Any]) -> bool:
    labels = engine.get("labels")
    if isinstance(labels, dict) and any(_SCRATCH_MARKER in str(v) for v in labels.values()):
        return True
    blob = f"{engine.get('name', '')} {engine.get('displayName', '')}"
    return _SCRATCH_MARKER in blob
