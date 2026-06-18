"""Render a :class:`SweepResult` to JSON (the ``--format json`` output)."""

from __future__ import annotations

import json

from ..models import SweepResult


def render_json(result: SweepResult) -> str:
    return json.dumps(result.model_dump(mode="json"), indent=2)
