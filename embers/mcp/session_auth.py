"""Bind MCP auth to a session, not to every write.

Call ember_register, then ember_start_session once with the token.
Later tools on this connection use that session. They may also pass
session_id instead of the token (survives a new process).
"""

from __future__ import annotations

import os

from ..core.types import SessionStatus
from . import server

_INSTALLED = False

_WHOAMI = {
    "name": "ember_whoami",
    "description": (
        "Show the bound agent and session. "
        "No token needed once ember_start_session has run."
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
    _patch_init()
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
                "Open a session and bind this MCP connection. "
                "Pass agent_id and token once. Later tools use this session "
                "and do not need the token again. Pass session_id on a new "
                "connection to resume."
            )


def _patch_init() -> None:
    orig = server.EmberMCP.__init__

    def __init__(self, *args, **kwargs):
        orig(self, *args, **kwargs)
        self._identity = None
        self._session_id = None

    server.EmberMCP.__init__ = __init__


def _patch_auth() -> None:
    def _auth(self, args: dict):
        args = args or {}
        agent_id = args.get("agent_id") or os.environ.get("EMBER_AGENT_ID")
        token = args.get("token") or os.environ.get("EMBER_TOKEN")
        if agent_id and token:
            ident = self.registry.authenticate(agent_id, token)
            self._identity = ident
            return ident

        sid = args.get("session_id") or getattr(self, "_session_id", None)
        if sid:
            session = self.db.get_session(sid)
            if session is None:
                raise PermissionError("unknown session")
            if session.status != SessionStatus.ACTIVE:
                raise PermissionError("session is not active")
            ident = self.registry.get(session.agent_id)
            if ident is None:
                raise PermissionError("unknown agent")
            self._identity = ident
            self._session_id = sid
            return ident

        ident = getattr(self, "_identity", None)
        if ident is not None:
            return ident

        raise PermissionError(
            "No session. Call ember_start_session once with agent_id and "
            "token, or pass session_id."
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
            self._identity = ident
            return server._text({
                "agent_id": ident.agent_id,
                "token": token,
                "provider": ident.provider,
                "model": ident.model,
                "note": "Call ember_start_session once with this pair. Later tools use the session.",
            })

        if name == "ember_start_session":
            agent = self._auth(args)
            sid = self.db.start_session(
                agent_id=agent.agent_id,
                task=args.get("task", ""),
                namespace=args.get("namespace") or self.protocol.namespace,
            )
            self._identity = agent
            self._session_id = sid
            return server._text({
                "session_id": sid,
                "agent_id": agent.agent_id,
            })

        if name == "ember_whoami":
            try:
                agent = self._auth(args) if (
                    args.get("session_id") or args.get("agent_id") or args.get("token")
                    or getattr(self, "_identity", None)
                    or getattr(self, "_session_id", None)
                ) else None
            except PermissionError as e:
                return server._err(str(e))
            if agent is None:
                return server._err(
                    "No session. Call ember_start_session once with agent_id and token."
                )
            return server._text({
                "agent_id": agent.agent_id,
                "name": agent.name,
                "session_id": getattr(self, "_session_id", None),
            })

        if not args.get("session_id") and getattr(self, "_session_id", None):
            args["session_id"] = self._session_id

        return orig(self, name, args)

    server.EmberMCP._call = _call
