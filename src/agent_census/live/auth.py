"""Token acquisition for live discovery.

Two flows, both yielding a ``get_token(scope)`` callable shared across every
Azure/Dataverse resource a sweep touches:

* ``device``  — interactive device-code; runs as the *signed-in user*. Sign in
  once, then later resource scopes are obtained silently from the cached token.
* ``app``     — client-credentials; runs as a service principal (headless/CI).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

import msal

from ..errors import DiscoveryError


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


def build_auth(
    *,
    mode: str,
    client_id: str | None = None,
    tenant: str | None = None,
    client_secret: str | None = None,
    prompt: Callable[[str], None] | None = None,
) -> TokenProvider:
    """Construct the requested token provider, validating required inputs."""
    if mode == "cli":
        # Reuses `az login`; no app registration / client id needed.
        return AzureCliAuth(tenant=tenant)

    if not client_id:
        raise DiscoveryError(
            "live discovery needs --client-id (an Entra app registration client id)"
        )
    if mode == "device":
        return DeviceCodeAuth(client_id, tenant, prompt=prompt)
    if mode == "app":
        if not client_secret or not tenant:
            raise DiscoveryError("app auth requires --tenant and --client-secret")
        return ClientCredentialAuth(client_id, client_secret, tenant)
    raise DiscoveryError(f"unknown auth mode: {mode!r}")
