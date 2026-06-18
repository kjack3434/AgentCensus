"""Shared exception types."""

from __future__ import annotations


class DiscoveryError(Exception):
    """Raised when a source cannot complete discovery (auth, network, config)."""
