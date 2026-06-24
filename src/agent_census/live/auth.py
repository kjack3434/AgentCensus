"""Token acquisition for live discovery.

Auth is chosen as a **strategy** and each provider implements it with its own
mechanism, so one ``--auth`` value works across clouds (and across both in a
single ``--source all`` run):

* ``cli``    — reuse the local cloud CLI session (Microsoft: ``az login`` ·
  Google: ``gcloud auth``). No app registration / key.
* ``app``    — non-interactive service credential (Microsoft: Entra app +
  secret · Google: service-account JSON key). For CI / automation.
* ``device`` — Entra device-code interactive sign-in (**Microsoft only**).
* ``adc``    — Application Default Credentials (**Google only**; needs the
  optional ``gcp`` extra).

``build_microsoft_auth`` / ``build_gcp_auth`` resolve a strategy to a provider
*or* ``None`` (with a human note) when it isn't configured/signed-in, so the
caller can skip a provider gracefully rather than fail the whole run. Each
provider yields a ``get_token(scope)`` callable; Google reuses one broad
``cloud-platform`` token across its APIs.
"""

from __future__ import annotations

import importlib
import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

import msal

from ..errors import DiscoveryError

# GCP needs exactly one broad audience; the read-only variant is rejected by
# aiplatform, so read-only posture must come from IAM Viewer roles, not the scope.
GCP_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


def _to_delegated_scope(scope: str) -> str:
    """Adapt a Dataverse scope for public/delegated clients.

    Dataverse (``*.dynamics.com``) requires the ``user_impersonation`` scope for
    public clients (device-code, Azure CLI); confidential clients use ``.default``.
    ARM and the Foundry data plane keep ``.default``.
    See Microsoft Learn: "Use OAuth with Dataverse".
    """
    if scope.endswith("/.default") and urlsplit(scope).netloc.endswith(".dynamics.com"):
        return scope[: -len("/.default")] + "/user_impersonation"
    return scope


@runtime_checkable
class TokenProvider(Protocol):
    def get_token(self, scope: str) -> str: ...


def _authority(tenant: str | None) -> str:
    # "organizations" lets a user sign in against their own tenant in device mode.
    return f"https://login.microsoftonline.com/{tenant or 'organizations'}"


def _default_prompt(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


class DeviceCodeAuth:
    """Interactive device-code flow — acts as the signed-in user."""

    def __init__(
        self,
        client_id: str,
        tenant: str | None = None,
        *,
        prompt: Callable[[str], None] | None = None,
    ) -> None:
        self._app = msal.PublicClientApplication(client_id, authority=_authority(tenant))
        self._prompt = prompt or _default_prompt
        self._account = None

    def get_token(self, scope: str) -> str:
        scope = _to_delegated_scope(scope)
        if self._account is not None:
            silent = self._app.acquire_token_silent([scope], account=self._account)
            if silent and "access_token" in silent:
                return silent["access_token"]

        flow = self._app.initiate_device_flow(scopes=[scope])
        if "user_code" not in flow:
            raise DiscoveryError(
                "could not start device-code sign-in: " + str(flow.get("error_description") or flow)
            )
        self._prompt(flow["message"])
        result = self._app.acquire_token_by_device_flow(flow) or {}  # blocks until completed
        if "access_token" not in result:
            raise DiscoveryError(
                "device-code sign-in failed: "
                + str(result.get("error_description") or result.get("error") or "unknown error")
            )
        accounts = self._app.get_accounts()
        self._account = accounts[0] if accounts else None
        return result["access_token"]


class ClientCredentialAuth:
    """App-only / service-principal flow."""

    def __init__(self, client_id: str, client_secret: str, tenant: str) -> None:
        if not tenant:
            raise DiscoveryError("app auth requires a tenant id")
        self._app = msal.ConfidentialClientApplication(
            client_id,
            authority=_authority(tenant),
            client_credential=client_secret,
        )

    def get_token(self, scope: str) -> str:
        result = self._app.acquire_token_for_client([scope]) or {}
        if "access_token" not in result:
            raise DiscoveryError(
                f"authentication failed for {scope}: "
                + str(result.get("error_description") or result.get("error") or "unknown error")
            )
        return result["access_token"]


class AzureCliAuth:
    """Reuse an existing ``az login`` session — no app registration required.

    Borrows the Azure CLI's own first-party client by shelling out to
    ``az account get-access-token`` per resource. Best for a dev one-off run.
    """

    def __init__(self, tenant: str | None = None) -> None:
        if shutil.which("az") is None:
            raise DiscoveryError(
                "Azure CLI ('az') not found — install it and run `az login`, or use --auth device"
            )
        self._tenant = tenant
        self._cache: dict[str, str] = {}

    def get_token(self, scope: str) -> str:
        scope = _to_delegated_scope(scope)
        if scope in self._cache:
            return self._cache[scope]
        # v2 --scope so we can request Dataverse user_impersonation (not /.default).
        cmd = ["az", "account", "get-access-token", "--scope", scope, "--output", "json"]
        if self._tenant:
            cmd += ["--tenant", self._tenant]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as exc:
            raise DiscoveryError(f"failed to run az: {exc}") from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            raise DiscoveryError(
                f"az token acquisition failed for {scope}: {detail}. "
                "Run `az login` first, or use --auth device."
            )
        try:
            token = json.loads(proc.stdout)["accessToken"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise DiscoveryError(f"could not parse az token output: {exc}") from exc
        self._cache[scope] = token
        return token


# ── Google Cloud ─────────────────────────────────────────────────────────────

ARM_PROBE_SCOPE = "https://management.azure.com/.default"

# Hosts a service-account key is allowed to exchange tokens against. A tampered
# key could otherwise point ``token_uri`` at an attacker endpoint and exfiltrate
# the signed assertion, so we validate the host before google-auth ever uses it.
_GOOGLE_TOKEN_HOSTS = frozenset({"oauth2.googleapis.com", "accounts.google.com"})


def _validate_google_token_uri(uri: str | None) -> None:
    if not uri:
        return
    host = (urlsplit(uri).hostname or "").lower()
    if host in _GOOGLE_TOKEN_HOSTS or host.endswith(".googleapis.com"):
        return
    raise DiscoveryError(f"refusing service-account key: untrusted token endpoint host {host!r}")


def _require_google_auth() -> Any:
    """Import google-auth lazily; the base install stays free of the heavy tree."""
    try:
        return importlib.import_module("google.auth.transport.requests")
    except ImportError as exc:  # pragma: no cover - exercised via the extra
        raise DiscoveryError(
            "Google auth needs the optional 'gcp' extra — install it with "
            "`uv sync --extra gcp` or `pip install 'agentcensus[gcp]'`"
        ) from exc


class GcloudCliAuth:
    """Reuse an existing ``gcloud`` session — no service-account key required.

    Shells out to ``gcloud auth print-access-token``. GCP uses one broad
    cloud-platform audience, so the token is fetched once and reused for every
    API the sweep touches (the ``scope`` argument is intentionally ignored).
    """

    def __init__(self, *, impersonate: str | None = None) -> None:
        if shutil.which("gcloud") is None:
            raise DiscoveryError(
                "gcloud CLI not found — install it and run `gcloud auth login`, "
                "or use --auth app with --gcp-key-file"
            )
        self._impersonate = impersonate
        self._token: str | None = None
        # A plain gcloud session is *user* credentials and MUST send
        # x-goog-user-project, or Discovery Engine / Dialogflow 403 as
        # SERVICE_DISABLED (billed to gcloud's shared project). An *impersonated*
        # token is the service account's — it carries its own quota project and
        # would 403 on the header without serviceusage.services.use, so omit it.
        self.needs_quota_project = impersonate is None

    def get_token(self, scope: str) -> str:
        if self._token is not None:
            return self._token
        cmd = ["gcloud", "auth", "print-access-token"]
        if self._impersonate:
            cmd += ["--impersonate-service-account", self._impersonate]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as exc:
            raise DiscoveryError(f"failed to run gcloud: {exc}") from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            raise DiscoveryError(
                f"gcloud token acquisition failed: {detail}. "
                "Run `gcloud auth login` first, or use --auth app with --gcp-key-file."
            )
        token = proc.stdout.strip()
        if not token:
            raise DiscoveryError("gcloud returned an empty access token")
        self._token = token
        return token

    @property
    def default_project(self) -> str | None:
        """Active project from ``gcloud config`` — used when --project is omitted."""
        try:
            proc = subprocess.run(
                ["gcloud", "config", "get-value", "project"],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        val = proc.stdout.strip() if proc.returncode == 0 else ""
        # gcloud prints "(unset)" to stderr and an empty stdout when unset.
        return val or None


class _GoogleCredsAuth:
    """Wrap a google-auth Credentials object behind the ``get_token`` contract."""

    def __init__(
        self,
        creds: Any,
        request: Any,
        default_project: str | None,
        *,
        needs_quota_project: bool = False,
    ) -> None:
        self._creds = creds
        self._request = request
        self.default_project = default_project
        # User-flavored ADC needs the quota-project header; a service-account
        # key carries its own quota project and must NOT send it (the SA may lack
        # serviceusage.services.use on the scanned project).
        self.needs_quota_project = needs_quota_project

    def get_token(self, scope: str) -> str:
        if not getattr(self._creds, "valid", False):
            try:
                self._creds.refresh(self._request)
            except Exception as exc:  # google-auth raises a broad set here
                raise DiscoveryError(f"Google token refresh failed: {exc}") from exc
        token = getattr(self._creds, "token", None)
        if not token:
            raise DiscoveryError("Google credentials returned no access token")
        return str(token)


def google_service_account_auth(key_file: str) -> _GoogleCredsAuth:
    """Build a token provider from a service-account JSON key (optional ``gcp`` extra)."""
    requests_tp = _require_google_auth()
    service_account = importlib.import_module("google.oauth2.service_account")
    try:
        with open(key_file, encoding="utf-8") as fh:
            info = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise DiscoveryError(f"could not read service-account key {key_file!r}: {exc}") from exc
    if not isinstance(info, dict):
        raise DiscoveryError(f"service-account key {key_file!r} is not a JSON object")
    _validate_google_token_uri(info.get("token_uri"))
    _validate_google_token_uri(info.get("auth_uri"))
    try:
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=[GCP_CLOUD_PLATFORM_SCOPE]
        )
    except (ValueError, KeyError) as exc:
        raise DiscoveryError(f"invalid service-account key {key_file!r}: {exc}") from exc
    # Service accounts carry their own quota project — don't send the header.
    return _GoogleCredsAuth(creds, requests_tp.Request(), info.get("project_id"))


def google_adc_auth() -> _GoogleCredsAuth:
    """Build a token provider from Application Default Credentials (optional ``gcp`` extra)."""
    requests_tp = _require_google_auth()
    google_auth = importlib.import_module("google.auth")
    try:
        creds, project = google_auth.default(scopes=[GCP_CLOUD_PLATFORM_SCOPE])
    except Exception as exc:  # DefaultCredentialsError + friends
        raise DiscoveryError(
            f"could not load Application Default Credentials: {exc}. "
            "Run `gcloud auth application-default login`."
        ) from exc
    # ADC can resolve to user creds (which must send the quota-project header) OR
    # to a service account (GCE / Cloud Run / GOOGLE_APPLICATION_CREDENTIALS), which
    # carries its own quota project and would 403 on the header without
    # serviceusage.services.use. Service-account / compute creds expose
    # ``service_account_email``; user credentials do not.
    is_user = not hasattr(creds, "service_account_email")
    return _GoogleCredsAuth(creds, requests_tp.Request(), project, needs_quota_project=is_user)


# ── Strategy-based resolvers (one --auth strategy, applied per provider) ───────
#
# Auth is chosen as a *strategy* and each provider implements it with its own
# mechanism, so one flag works across clouds:
#   cli    -> reuse the local cloud CLI session  (Microsoft: az · Google: gcloud)
#   app    -> non-interactive service credential (Microsoft: Entra app+secret ·
#                                                  Google: service-account key)
#   device -> Entra device-code interactive sign-in (Microsoft only)
#   adc    -> Application Default Credentials        (Google only)
#
# Each resolver returns ``(provider | None, status note)``. ``None`` means "not
# configured / not signed in for this strategy" — the caller skips that provider
# (with the note) instead of hard-failing, so a single-cloud user still gets a
# report. For the ``cli`` strategy the resolver probes the CLI session so the
# note can honestly say "signed in" vs "not signed in".

AuthResult = tuple[TokenProvider | None, str]


def build_microsoft_auth(
    strategy: str,
    *,
    client_id: str | None = None,
    tenant: str | None = None,
    client_secret: str | None = None,
    prompt: Callable[[str], None] | None = None,
) -> AuthResult:
    if strategy == "cli":
        if shutil.which("az") is None:
            return None, "Azure CLI (az) not found"
        auth = AzureCliAuth(tenant=tenant)
        try:
            auth.get_token(ARM_PROBE_SCOPE)  # verifies an active `az login`
        except DiscoveryError:
            return None, "Azure CLI found but not signed in — run `az login`"
        return auth, "Azure CLI (az login)"
    if strategy == "device":
        if not client_id:
            return None, "device sign-in needs --client-id"
        return DeviceCodeAuth(client_id, tenant, prompt=prompt), "Entra device code"
    if strategy == "app":
        if not (client_id and tenant and client_secret):
            return None, "app auth needs --client-id, --tenant and --client-secret"
        return (
            ClientCredentialAuth(client_id, client_secret, tenant),
            "Entra app (service principal)",
        )
    if strategy == "adc":
        return None, "ADC is a Google-only strategy"
    return None, f"unknown auth strategy {strategy!r}"


def build_gcp_auth(
    strategy: str,
    *,
    gcp_key_file: str | None = None,
    gcp_impersonate: str | None = None,
) -> AuthResult:
    if strategy == "cli":
        if shutil.which("gcloud") is None:
            return None, "gcloud CLI not found"
        auth = GcloudCliAuth(impersonate=gcp_impersonate)
        try:
            auth.get_token(GCP_CLOUD_PLATFORM_SCOPE)  # verifies an active gcloud session
        except DiscoveryError:
            return None, "gcloud found but not signed in — run `gcloud auth login`"
        return auth, "gcloud (gcloud auth)"
    if strategy == "app":
        if not gcp_key_file:
            return None, "service-account auth needs --gcp-key-file"
        try:
            return google_service_account_auth(gcp_key_file), "service-account key"
        except DiscoveryError as exc:
            return None, str(exc)
    if strategy == "adc":
        try:
            return google_adc_auth(), "Application Default Credentials"
        except DiscoveryError as exc:
            return None, str(exc)
    if strategy == "device":
        return None, "device sign-in is Microsoft-only (use cli, app or adc for Google)"
    return None, f"unknown auth strategy {strategy!r}"
