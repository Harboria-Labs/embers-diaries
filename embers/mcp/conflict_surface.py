"""Compatibility installer for the conflict MCP surface.

Conflict schemas now live in ``mcp.tools`` and handlers delegate through the
shared provider-neutral ``integration.conflict_protocol`` contract. The
installer remains as a no-op because older embedding code imports and calls it.
"""

from __future__ import annotations


_INSTALLED = False


def install() -> None:
    global _INSTALLED
    _INSTALLED = True
