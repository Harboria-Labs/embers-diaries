"""Bind MCP auth to a session id, not to every write.

Register once. ember_start_session once with the token.
Every later call passes session_id. That id is paired to the agent
in the store. Do not reuse the last identity on this process —
the HTTP /mcp adapter shares one EmberMCP across every client.
"""

from __future__ import annotations

import os

from ..core.types import SessionStatus
from . import server

_INSTALLED = False

_WHOAMI = {
    "name": "ember_whoami",
    "description": (
        "Show the agent for this session_id. "
        "Pass session_id, or agent_id and token."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string"},
            "agent_id": {"type": "string"},
            "token": {"type": "string"},
        },
        "required": [],
    },
}


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _patch_schema()
    _patch_auth()
    _patch_call()
    _INSTALLED = True


def _patch_schema() -> None:
    names = {t.get("name") for t in server.TOOLS}
    if "ember_whoami" not in names:
        server.TOOLS.append(_WHOAMI)
    for tool in server.TOOLS:
        if tool.get("name") == "ember_start_session":
            tool["description"] = (
                "Open a session. Pass agent_id and token once. "
                "Keep session_id. Later tools pass session_id only — "
                "not the token."
            )


def _patch_auth() -> None:
    def _auth(self, args: dict):
        args = args or {}
        agent_id = args.get("agent_id") or os.environ.get("EMBER_AGENT_ID")
        token = args.get("token") or os.environ.get("EMBER_TOKEN")
        if agent_id and token:
            return self.registry.authenticate(agent_id, token)

        sid = args.get("session_id")
        if sid:
            session = self.db.get_session(sid)
            if session is None:
                raise PermissionError("unknown session")
            if session.status != SessionStatus.ACTIVE:
                raise PermissionError("session is not active")
            ident = self.registry.get(session.agent_id)
            if ident is None:
                raise PermissionError("unknown agent")
            return ident

        raise PermissionError(
            "Pass session_id, or agent_id and token on ember_start_session."
        )

    server.EmberMCP._auth = _auth


def _patch_call() -> None:
    orig = server.EmberMCP._call

    def _call(self, name: str, args: dict):
        args = dict(args or {})

        if name == "ember_register":
            ident, token = self.registry.register(
                name=args.get("name", ""),
                provider=args.get("provider", "unknown"),
                model=args.get("model", "unknown"),
            )
            return server._text({
                "agent_id": ident.agent_id,
                "token": token,
                "provider": ident.provider,
                "model": ident.model,
                "note": (
                    "Call ember_start_session once with this pair. "
                    "Later tools pass session_id only."
                ),
            })

        if name == "ember_start_session":
            agent = self._auth(args)
            sid = self.db.start_session(
                agent_id=agent.agent_id,
                task=args.get("task", ""),
                namespace=args.get("namespace") or self.protocol.namespace,
            )
            return server._text({
                "session_id": sid,
                "agent_id": agent.agent_id,
            })

        if name == "ember_whoami":
            try:
                agent = self._auth(args)
            except PermissionError as e:
                return server._err(str(e))
            return server._text({
                "agent_id": agent.agent_id,
                "name": agent.name,
                "session_id": args.get("session_id"),
            })

        return orig(self, name, args)

    server.EmberMCP._call = _call
