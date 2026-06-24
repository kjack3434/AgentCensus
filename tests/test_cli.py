import json
import re

from typer.testing import CliRunner

from agent_census.cli import app

runner = CliRunner()

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
# Wide terminal so Typer's Rich error box doesn't wrap mid-word; strip ANSI.
_WIDE = {"COLUMNS": "200"}


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


def test_sweep_demo_html(tmp_path):
    out = tmp_path / "r.html"
    res = runner.invoke(app, ["sweep", "--demo", "-o", str(out)])
    assert res.exit_code == 0, res.output
    assert out.exists()
    txt = out.read_text(encoding="utf-8")
    assert '<table id="agents">' in txt
    assert 'id="data"' in txt
    assert "Swept 16 agents" in res.output


def test_sweep_demo_json(tmp_path):
    out = tmp_path / "r.json"
    res = runner.invoke(app, ["sweep", "--demo", "-f", "json", "-o", str(out), "-q"])
    assert res.exit_code == 0
    obj = json.loads(out.read_text(encoding="utf-8"))
    assert obj["summary"]["total_agents"] == 16


def test_fail_on_critical_trips(tmp_path):
    out = tmp_path / "r.html"
    res = runner.invoke(app, ["sweep", "--demo", "-o", str(out), "-q", "--fail-on", "critical"])
    assert res.exit_code == 1
    assert out.exists()  # report still written before the gate fails


def test_missing_live_credentials(tmp_path):
    res = runner.invoke(
        app,
        ["sweep", "--source", "copilot-studio", "--auth", "device", "-o", str(tmp_path / "r.html")],
        env=_WIDE,
    )
    assert res.exit_code == 2
    assert "client-id" in _plain(res.output)


def test_app_auth_missing_secret(tmp_path):
    res = runner.invoke(
        app,
        [
            "sweep",
            "--source",
            "foundry",
            "--auth",
            "app",
            "--client-id",
            "c",
            "--tenant",
            "t",
            "-o",
            str(tmp_path / "r.html"),
        ],
        env=_WIDE,
    )
    assert res.exit_code == 2
    assert "secret" in _plain(res.output).lower()


def test_creates_parent_dirs(tmp_path):
    out = tmp_path / "nested" / "deep" / "r.html"
    res = runner.invoke(app, ["sweep", "--demo", "-o", str(out), "-q"])
    assert res.exit_code == 0
    assert out.exists()


def test_write_error_returns_exit_4(tmp_path):
    # --out pointing at an existing directory makes write_text fail (IsADirectoryError -> OSError)
    res = runner.invoke(app, ["sweep", "--demo", "-o", str(tmp_path), "-q"])
    assert res.exit_code == 4


def test_version():
    res = runner.invoke(app, ["version"])
    assert res.exit_code == 0
    assert res.output.strip()


def test_schema_is_valid_json():
    res = runner.invoke(app, ["schema"])
    assert res.exit_code == 0
    obj = json.loads(res.output)
    assert obj.get("title") == "SweepResult"
