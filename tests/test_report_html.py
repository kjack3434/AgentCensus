import json
from datetime import UTC, datetime

from agent_census.models import Agent, Summary, SweepMeta, SweepResult
from agent_census.report import render_html, render_json
from agent_census.sources import build_source

_TOKENS = (
    "/*__CSS__*/",
    "/*__JS__*/",
    "/*__DATA__*/",
    "__GENERATED_AT__",
    "__SOURCE__",
    "__TOOL_VERSION__",
)


def _data_blob(html: str) -> str:
    return html.split('id="data">', 1)[1].split("</script>", 1)[0]


def test_render_replaces_all_tokens_and_parses():
    r = build_source(["demo"]).scan()
    html = render_html(r)
    for tok in _TOKENS:
        assert tok not in html, f"token {tok} left in output"
    assert '<table id="agents">' in html
    assert "http://" not in html.replace("http://www.w3.org", "")  # no external requests
    blob = _data_blob(html)
    assert "</" not in blob
    assert json.loads(blob)["summary"]["total_agents"] == 16


def test_script_breakout_is_escaped():
    meta = SweepMeta(
        generated_at=datetime(2026, 1, 1, tzinfo=UTC), source="demo", tool_version="0.1.0"
    )
    payload = "</script><script>alert(1)</script>"
    a = Agent(name="x", external_id="i", source_system="copilot_studio", instructions=payload)
    r = SweepResult(meta=meta, summary=Summary(total_agents=1), agents=[a])
    html = render_html(r)
    blob = _data_blob(html)
    assert "</script>" not in blob
    assert "<\\/script>" in blob
    assert json.loads(blob)["agents"][0]["instructions"] == payload


def test_render_json_shape():
    obj = json.loads(render_json(build_source(["demo"]).scan()))
    assert obj["schema_version"] == "agentcensus/v1"
    assert obj["summary"]["total_agents"] == 16
    assert obj["summary"]["findings_by_severity"]["critical"] >= 1
