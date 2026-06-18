"""Report renderers."""

from __future__ import annotations

from .html import render_html
from .json_out import render_json

__all__ = ["render_html", "render_json"]
