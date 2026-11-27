from embers._native import atomic_replace as _quota_atomic_replace
"""
Ember's Diaries — Namespace Manager
Logical partitions within a single store.
Namespace metadata, access control, schema hints.

Access control model:
  - PUBLIC:   any caller can read and write
  - PRIVATE:  only the owner (written_by on create) can read/write
  - INTERNAL: any caller can read, only the owner can write

Access is checked by caller identity (a string like "lila", "agent_7", "system").
The namespace owner is the caller who created it.
"""

import uuid
from datetime import datetime, timezone
from pathlib import Path
from ..core.types import AccessLevel
from ..storage.format import encode_index, decode_index


class AccessDeniedError(PermissionError):
    """Raised when a caller lacks permission for a namespace operation."""
    pass


class NamespaceInfo:
    def __init__(self, name: str, description: str = "",
                 access_level: AccessLevel = AccessLevel.PRIVATE,
                 owner: str = "system",
                 schema_hint: dict | None = None,
                 created_at: datetime | None = None,
                 allowed_writers: list[str] | None = None,
                 allowed_readers: list[str] | None = None):
        self.name = name
        self.description = description
        self.access_level = access_level
        self.owner = owner
        self.schema_hint = schema_hint
        self.created_at = created_at or datetime.now(timezone.utc)
        self.allowed_writers = allowed_writers or []
        self.allowed_readers = allowed_readers or []

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "access_level": self.access_level.value,
            "owner": self.owner,
            "schema_hint": self.schema_hint,
            "created_at": self.created_at.isoformat(),
            "allowed_writers": self.allowed_writers,
            "allowed_readers": self.allowed_readers,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "NamespaceInfo":
        return cls(
            name=d["name"],
            description=d.get("description", ""),
            access_level=AccessLevel(d.get("access_level", "private")),
            owner=d.get("owner", "system"),
            schema_hint=d.get("schema_hint"),
            created_at=datetime.fromisoformat(d["created_at"]) if d.get("created_at") else None,
            allowed_writers=d.get("allowed_writers", []),
            allowed_readers=d.get("allowed_readers", []),
        )


class NamespaceManager:
    """
    Manages namespace creation, listing, and access control.
    Multiple AI systems can share one store via separate namespaces
    with no data bleed between them.

    Access control:
      - PUBLIC:   anyone can read/write
      - INTERNAL: anyone can read, only owner + allowed_writers can write
      - PRIVATE:  only owner + allowed_readers can read,
                  only owner + allowed_writers can write
    """

    def __init__(self, store_path: Path):
        self._path = store_path / "namespaces"
        self._path.mkdir(parents=True, exist_ok=True)
        self._namespaces: dict[str, NamespaceInfo] = {}
        self._load()

    def _load(self):
        index_file = self._path / "registry.json"
        if not index_file.exists():
            return
        try:
            data = decode_index(index_file.read_bytes())
            for name, info in data.get("namespaces", {}).items():
                self._namespaces[name] = NamespaceInfo.from_dict(info)
        except Exception:
            pass

    def persist(self):
        data = {
            "namespaces": {
                name: info.to_dict() for name, info in self._namespaces.items()
            }
        }
        index_file = self._path / "registry.json"
        _quota_atomic_replace(str(index_file), encode_index(data))

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def create(self, name: str, description: str = "",
               access_level: AccessLevel = AccessLevel.PRIVATE,
               owner: str = "system",
               schema_hint: dict | None = None) -> NamespaceInfo:
        if name in self._namespaces:
            raise ValueError(f"Namespace '{name}' already exists")
        ns = NamespaceInfo(name, description, access_level, owner, schema_hint)
        self._namespaces[name] = ns
        self.persist()
        return ns

    def get(self, name: str) -> NamespaceInfo | None:
        return self._namespaces.get(name)

    def list_all(self) -> list[NamespaceInfo]:
        return list(self._namespaces.values())

    def exists(self, name: str) -> bool:
        return name in self._namespaces

    def delete(self, name: str) -> bool:
        """Remove namespace metadata (records remain in store)."""
        if name in self._namespaces:
            del self._namespaces[name]
            self.persist()
            return True
        return False

    def count(self) -> int:
        return len(self._namespaces)

    # ── Access control ────────────────────────────────────────────────────────

    def check_read(self, namespace: str, caller: str) -> bool:
        """
        Check if caller has read access to a namespace.
        Returns True if allowed. Unregistered namespaces are open by default.
        """
        ns = self._namespaces.get(namespace)
        if ns is None:
            return True  # Unregistered namespace — open access

        if ns.access_level == AccessLevel.PUBLIC:
            return True
        if ns.access_level == AccessLevel.INTERNAL:
            return True  # Anyone can read INTERNAL
        # PRIVATE — owner + allowed_readers only
        return caller == ns.owner or caller in ns.allowed_readers

    def check_write(self, namespace: str, caller: str) -> bool:
        """
        Check if caller has write access to a namespace.
        Returns True if allowed. Unregistered namespaces are open by default.
        """
        ns = self._namespaces.get(namespace)
        if ns is None:
            return True  # Unregistered namespace — open access

        if ns.access_level == AccessLevel.PUBLIC:
            return True
        # INTERNAL or PRIVATE — owner + allowed_writers only
        return caller == ns.owner or caller in ns.allowed_writers

    def require_read(self, namespace: str, caller: str):
        """Raise AccessDeniedError if caller cannot read."""
        if not self.check_read(namespace, caller):
            raise AccessDeniedError(
                f"Caller '{caller}' does not have read access to namespace '{namespace}'")

    def require_write(self, namespace: str, caller: str):
        """Raise AccessDeniedError if caller cannot write."""
        if not self.check_write(namespace, caller):
            raise AccessDeniedError(
                f"Caller '{caller}' does not have write access to namespace '{namespace}'")

    def grant_read(self, namespace: str, caller: str):
        """Grant read access to a caller."""
        ns = self._namespaces.get(namespace)
        if ns and caller not in ns.allowed_readers:
            ns.allowed_readers.append(caller)
            self.persist()

    def grant_write(self, namespace: str, caller: str):
        """Grant write access to a caller."""
        ns = self._namespaces.get(namespace)
        if ns and caller not in ns.allowed_writers:
            ns.allowed_writers.append(caller)
            self.persist()

    def revoke_read(self, namespace: str, caller: str):
        """Revoke read access from a caller."""
        ns = self._namespaces.get(namespace)
        if ns and caller in ns.allowed_readers:
            ns.allowed_readers.remove(caller)
            self.persist()

    def revoke_write(self, namespace: str, caller: str):
        """Revoke write access from a caller."""
        ns = self._namespaces.get(namespace)
        if ns and caller in ns.allowed_writers:
            ns.allowed_writers.remove(caller)
            self.persist()
