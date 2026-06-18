"""Auth flows with MSAL mocked out (no real sign-in)."""

import pytest

import agent_census.live.auth as auth_mod
from agent_census.errors import DiscoveryError
from agent_census.live.auth import DeviceCodeAuth, build_auth


class FakePublicApp:
    last_scopes = None

    def __init__(self, client_id, authority=None):
        self.client_id = client_id

    def acquire_token_silent(self, scopes, account=None):
        return None

    def initiate_device_flow(self, scopes):
        type(self).last_scopes = scopes
        return {"user_code": "ABC", "message": "go to microsoft.com/devicelogin"}

    def acquire_token_by_device_flow(self, flow):
        return {"access_token": "tok-" + flow["user_code"]}

    def get_accounts(self):
        return [{"home_account_id": "x"}]


class FakeConfApp:
    def __init__(self, client_id, authority=None, client_credential=None):
        pass

    def acquire_token_for_client(self, scopes):
        return {"access_token": "app-tok"}


def test_device_flow_prompts_and_returns_token(monkeypatch):
    monkeypatch.setattr(auth_mod.msal, "PublicClientApplication", FakePublicApp)
    prompts = []
    auth = DeviceCodeAuth("cid", "tenant", prompt=prompts.append)
    assert auth.get_token("scope1") == "tok-ABC"
    assert prompts and "devicelogin" in prompts[0]


def test_device_flow_dataverse_uses_user_impersonation(monkeypatch):
    monkeypatch.setattr(auth_mod.msal, "PublicClientApplication", FakePublicApp)
    auth = DeviceCodeAuth("cid", "tenant", prompt=lambda _m: None)
    auth.get_token("https://org.crm.dynamics.com/.default")
    assert FakePublicApp.last_scopes == ["https://org.crm.dynamics.com/user_impersonation"]


def test_device_flow_uses_silent_after_first(monkeypatch):
    class App(FakePublicApp):
        def acquire_token_silent(self, scopes, account=None):
            return {"access_token": "silent"} if account else None

    monkeypatch.setattr(auth_mod.msal, "PublicClientApplication", App)
    auth = DeviceCodeAuth("cid", prompt=lambda _m: None)
    assert auth.get_token("s1") == "tok-ABC"  # first: interactive
    assert auth.get_token("s2") == "silent"  # second resource: silent


def test_app_flow(monkeypatch):
    monkeypatch.setattr(auth_mod.msal, "ConfidentialClientApplication", FakeConfApp)
    auth = build_auth(mode="app", client_id="c", tenant="t", client_secret="s")
    assert auth.get_token("scope") == "app-tok"


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_cli_auth_returns_token(monkeypatch):
    monkeypatch.setattr(auth_mod.shutil, "which", lambda name: "/usr/bin/az")
    monkeypatch.setattr(
        auth_mod.subprocess, "run", lambda *a, **k: _Proc(stdout='{"accessToken": "cli-tok"}')
    )
    auth = build_auth(mode="cli")  # no client_id required
    assert auth.get_token("https://management.azure.com/.default") == "cli-tok"


def test_cli_auth_dataverse_uses_user_impersonation_scope(monkeypatch):
    monkeypatch.setattr(auth_mod.shutil, "which", lambda name: "/usr/bin/az")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Proc(stdout='{"accessToken": "t"}')

    monkeypatch.setattr(auth_mod.subprocess, "run", fake_run)
    build_auth(mode="cli").get_token("https://org.crm.dynamics.com/.default")
    i = captured["cmd"].index("--scope")
    # Dataverse is a public-client resource → user_impersonation, not /.default
    assert captured["cmd"][i + 1] == "https://org.crm.dynamics.com/user_impersonation"


def test_cli_auth_arm_keeps_default_scope(monkeypatch):
    monkeypatch.setattr(auth_mod.shutil, "which", lambda name: "/usr/bin/az")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Proc(stdout='{"accessToken": "t"}')

    monkeypatch.setattr(auth_mod.subprocess, "run", fake_run)
    build_auth(mode="cli").get_token("https://management.azure.com/.default")
    i = captured["cmd"].index("--scope")
    assert captured["cmd"][i + 1] == "https://management.azure.com/.default"


def test_cli_auth_missing_az(monkeypatch):
    monkeypatch.setattr(auth_mod.shutil, "which", lambda name: None)
    with pytest.raises(DiscoveryError):
        build_auth(mode="cli")


def test_cli_auth_login_failure(monkeypatch):
    monkeypatch.setattr(auth_mod.shutil, "which", lambda name: "/usr/bin/az")
    monkeypatch.setattr(
        auth_mod.subprocess,
        "run",
        lambda *a, **k: _Proc(returncode=1, stderr="Please run 'az login'"),
    )
    auth = build_auth(mode="cli")
    with pytest.raises(DiscoveryError):
        auth.get_token("https://ai.azure.com/.default")


def test_build_auth_requires_client_id():
    with pytest.raises(DiscoveryError):
        build_auth(mode="device", client_id=None)


def test_build_auth_app_requires_secret_and_tenant():
    with pytest.raises(DiscoveryError):
        build_auth(mode="app", client_id="c", tenant="t", client_secret=None)
    with pytest.raises(DiscoveryError):
        build_auth(mode="app", client_id="c", tenant=None, client_secret="s")


def test_build_auth_unknown_mode():
    with pytest.raises(DiscoveryError):
        build_auth(mode="bogus", client_id="c")
