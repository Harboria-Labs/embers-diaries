"""Canonical record hashing and integrity verification."""

from __future__ import annotations

import base64
import hashlib
import json
import math
from typing import Any


class RecordIntegrityError(ValueError):
    """Raised when persisted record content no longer matches its hash."""


try:
    from embers._native import BACKEND as HASH_BACKEND
    from embers._native import sha256_hex as _sha256_hex
except ImportError:
    HASH_BACKEND = "python-fallback"

    def _sha256_hex(canonical_bytes: bytes) -> str:
        return hashlib.sha256(canonical_bytes).hexdigest()


def _normalize(value: Any) -> Any:
    """Convert supported values to a deterministic, JSON-safe structure."""
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Canonical record content cannot contain NaN or infinity")
        return value
    if isinstance(value, bytes):
        return {"$bytes": base64.b64encode(value).decode("ascii")}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("Canonical record dictionaries require string keys")
        return {key: _normalize(value[key]) for key in sorted(value)}
    raise TypeError(f"Unsupported canonical record value: {type(value).__name__}")


def canonical_bytes(payload: dict[str, Any]) -> bytes:
    normalized = _normalize(payload)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def content_hash(payload: dict[str, Any]) -> str:
    return _sha256_hex(canonical_bytes(payload))

