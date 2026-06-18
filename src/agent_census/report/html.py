"""Render a :class:`SweepResult` to a single self-contained HTML file.

No CDN, no build step, no templating engine: the shell ``template.html`` carries
comment-token placeholders that we string-replace with the CSS, JS, and an inline
JSON blob. The data is injected *last* and ``</`` is escaped to ``<\\/`` so agent
text can never break out of the ``<script type="application/json">`` tag.
"""

from __future__ import annotations

import json
from importlib.resources import files

from ..models import SweepResult

_ASSETS = "agent_census.report.assets"


def _asset(name: str) -> str:
    return (files(_ASSETS) / name).read_text(encoding="utf-8")


def render_html(result: SweepResult) -> str:
    template = _asset("template.html")
    css = _asset("report.css")
    js = _asset("report.js")
    data_json = json.dumps(result.model_dump(mode="json")).replace("</", "<\\/")

    return (
        template.replace("/*__CSS__*/", css)
        .replace("/*__JS__*/", js)
        .replace("__GENERATED_AT__", result.meta.generated_at.isoformat())
        .replace("__TOOL_VERSION__", result.meta.tool_version)
        .replace("__SOURCE__", result.meta.source)
        .replace("/*__DATA__*/", data_json)  # last: untrusted-ish, never re-scanned
    )
