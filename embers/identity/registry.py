"""
Ember's Diaries — Agent identity (spec §21).

Issues an agent_id and a one-time token. The token is stored hashed.
A client cannot pick another agent's id and write as them.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from ..core.record import EmberRecord
from ..core.types import RecordType

AGENTS_NS = "_ember_agents"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class AgentIdentity:
    agent_id: str
    name: str
    provider: str
    model: str
    created_at: str


class AgentRegistry:
    def __init__(self, db):
        self._db = db

    def register(self, name: str, provider: str = "unknown",
                 model: str = "unknown") -> tuple[AgentIdentity, str]:
        if not name or not str(name).strip():
            raise ValueError("name is required")
        agent_id = f"agent-{uuid.uuid4()}"
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc).isoformat()
        record = EmberRecord(
            id=agent_id,
            namespace=AGENTS_NS,
            record_type=RecordType.DOCUMENT,
            data={
                "agent_id": agent_id,
                "name": name.strip(),
                "provider": provider,
                "model": model,
                "token_hash": _hash_token(token),
            },
            written_by="ember-registry",
            agent_id=agent_id,
            creation_reason="agent registration",
            tags=["agent", "identity"],
        )
        self._db.write(record)
        ident = AgentIdentity(
            agent_id=agent_id, name=name.strip(),
            provider=provider, model=model, created_at=now,
        )
        return ident, token

    def authenticate(self, agent_id: str, token: str) -> AgentIdentity:
        rec = self._db.get(agent_id, include_deprecated=False, include_superseded=False)
        if rec is None or rec.namespace != AGENTS_NS:
            raise PermissionError("unknown agent")
        data = rec.data or {}
        if data.get("token_hash") != _hash_token(token):
            raise PermissionError("invalid token")
        return AgentIdentity(
            agent_id=data.get("agent_id", agent_id),
            name=data.get("name", ""),
            provider=data.get("provider", "unknown"),
            model=data.get("model", "unknown"),
            created_at=rec.created_at.isoformat(),
        )

    def get(self, agent_id: str) -> AgentIdentity | None:
        rec = self._db.get(agent_id, include_deprecated=True, include_superseded=True)
        if rec is None or rec.namespace != AGENTS_NS:
            return None
        data = rec.data or {}
        return AgentIdentity(
            agent_id=data.get("agent_id", agent_id),
            name=data.get("name", ""),
            provider=data.get("provider", "unknown"),
            model=data.get("model", "unknown"),
            created_at=rec.created_at.isoformat(),
        )
