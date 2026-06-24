"""Auth flows with MSAL / CLIs mocked out (no real sign-in).

``build_microsoft_auth`` resolves a strategy to ``(provider, note)`` — ``None``
(not raising) when the strategy isn't configured/signed-in, so the caller can skip
a provider gracefully.
"""

import agent_census.live.auth as auth_mod
from agent_census.live.auth import DeviceCodeAuth, build_microsoft_auth


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


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ── device-code flow ─────────────────────────────────────────────────────────


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


# ── strategy resolver: Microsoft ──────────────────────────────────────────────


def test_app_strategy(monkeypatch):
    monkeypatch.setattr(auth_mod.msal, "ConfidentialClientApplication", FakeConfApp)
    provider, _ = build_microsoft_auth("app", client_id="c", tenant="t", client_secret="s")
    assert provider is not None
    assert provider.get_token("scope") == "app-tok"


def test_cli_strategy_returns_token(monkeypatch):
    monkeypatch.setattr(auth_mod.shutil, "which", lambda name: "/usr/bin/az")
    monkeypatch.setattr(
        auth_mod.subprocess, "run", lambda *a, **k: _Proc(stdout='{"accessToken": "cli-tok"}')
    )
    provider, _ = build_microsoft_auth("cli")  # probes `az` at build, then reuses the token
    assert provider is not None
    assert provider.get_token("https://management.azure.com/.default") == "cli-tok"


def test_cli_strategy_dataverse_uses_user_impersonation_scope(monkeypatch):
    monkeypatch.setattr(auth_mod.shutil, "which", lambda name: "/usr/bin/az")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Proc(stdout='{"accessToken": "t"}')

    monkeypatch.setattr(auth_mod.subprocess, "run", fake_run)
    provider, _ = build_microsoft_auth("cli")
    provider.get_token("https://org.crm.dynamics.com/.default")
    i = captured["cmd"].index("--scope")
    # Dataverse is a public-client resource → user_impersonation, not /.default
    assert captured["cmd"][i + 1] == "https://org.crm.dynamics.com/user_impersonation"


def test_cli_strategy_arm_keeps_default_scope(monkeypatch):
    monkeypatch.setattr(auth_mod.shutil, "which", lambda name: "/usr/bin/az")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Proc(stdout='{"accessToken": "t"}')

    monkeypatch.setattr(auth_mod.subprocess, "run", fake_run)
    provider, _ = build_microsoft_auth("cli")  # probe already used the ARM scope
    provider.get_token("https://management.azure.com/.default")
    i = captured["cmd"].index("--scope")
    assert captured["cmd"][i + 1] == "https://management.azure.com/.default"


def test_cli_strategy_missing_az(monkeypatch):
    monkeypatch.setattr(auth_mod.shutil, "which", lambda name: None)
    provider, note = build_microsoft_auth("cli")
    assert provider is None and "Azure CLI" in note


def test_cli_strategy_not_signed_in(monkeypatch):
    monkeypatch.setattr(auth_mod.shutil, "which", lambda name: "/usr/bin/az")
    monkeypatch.setattr(
        auth_mod.subprocess,
        "run",
        lambda *a, **k: _Proc(returncode=1, stderr="Please run 'az login'"),
    )
    provider, note = build_microsoft_auth("cli")  # probe fails -> not configured
    assert provider is None and "signed in" in note


def test_device_strategy_requires_client_id():
    provider, note = build_microsoft_auth("device", client_id=None)
    assert provider is None and "client-id" in note


def test_app_strategy_requires_secret_and_tenant():
    assert build_microsoft_auth("app", client_id="c", tenant="t", client_secret=None)[0] is None
    assert build_microsoft_auth("app", client_id="c", tenant=None, client_secret="s")[0] is None


def test_adc_strategy_is_google_only():
    provider, note = build_microsoft_auth("adc")
    assert provider is None and "Google" in note


def test_unknown_strategy():
    provider, note = build_microsoft_auth("bogus", client_id="c")
    assert provider is None and "unknown" in note.lower()
