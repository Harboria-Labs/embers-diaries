"""List proposals with evidence authors so CONSENSUS distance is visible."""

from __future__ import annotations

from ..core.types import ProposalStatus
from . import server
from ..core.proposal_listing import proposal_listing

_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    orig = server.EmberMCP._call

    def _call(self, name: str, args: dict):
        if name != "ember_list_proposals":
            return orig(self, name, args)
        self._auth(args)
        status = args.get("status")
        found = self.db.proposals(
            args.get("namespace") or self.protocol.namespace,
            status=ProposalStatus(status) if status else None)
        return server._text([proposal_listing(p) for p in found])

    server.EmberMCP._call = _call
    _INSTALLED = True
