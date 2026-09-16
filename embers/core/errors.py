"""
Ember's Diaries — Structured errors.

These carry enough context for a caller (often another agent) to understand a
failure and retry intelligently, rather than a bare message string.
"""

from __future__ import annotations


class StorageLimitError(Exception):
    """A configured storage quota rejected a write before it touched the WAL."""

    def __init__(self, limit: str, maximum: int, attempted: int):
        self.limit = limit
        self.maximum = maximum
        self.attempted = attempted
        super().__init__(
            f"Configured {limit} limit is {maximum} bytes; "
            f"the attempted value is {attempted} bytes")

    def to_dict(self) -> dict:
        return {
            "error": "StorageLimitError",
            "limit": self.limit,
            "maximum_bytes": self.maximum,
            "attempted_bytes": self.attempted,
        }


class ConcurrentModificationError(Exception):
    """Optimistic-concurrency precondition failed (spec §6).

    Raised when a caller supplies an `expected_hash` (the content hash of the
    version it read) but the record's CURRENT head no longer matches — someone
    else committed a new version in between. The write is refused so Agent B can
    never silently overwrite Agent A's work; B receives this structured error
    and can re-read, merge, and retry.

    Mirrors the spec's suggested shape:
        expected_hash / actual_hash / record_id /
        current_version / attempted_version
    """

    def __init__(self, record_id: str, expected_hash: str | None,
                 actual_hash: str | None, current_version: int | None = None,
                 attempted_version: int | None = None,
                 current_id: str | None = None):
        self.record_id = record_id
        self.expected_hash = expected_hash
        self.actual_hash = actual_hash
        self.current_version = current_version
        self.attempted_version = attempted_version
        # The id of the current head — what the caller should re-read to retry.
        self.current_id = current_id
        super().__init__(
            f"Concurrent modification of {record_id}: expected head hash "
            f"{expected_hash!r} but current head is {actual_hash!r} "
            f"(v{current_version}). The record was updated by someone else; "
            f"re-read {current_id or record_id} and retry.")

    def to_dict(self) -> dict:
        return {
            "error": "ConcurrentModificationError",
            "conflict_type": "storage",
            "status": "rejected",
            "record_id": self.record_id,
            "expected_hash": self.expected_hash,
            "actual_hash": self.actual_hash,
            "current_version": self.current_version,
            "attempted_version": self.attempted_version,
            "current_id": self.current_id,
        }
