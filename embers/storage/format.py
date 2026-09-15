"""
Ember's Diaries — Storage Format
Binary serialization using MessagePack.
Fast, compact, language-agnostic.
Falls back to JSON if msgpack is not installed.
"""

import json
from typing import Any


def _try_msgpack():
    try:
        import msgpack
        return msgpack
    except ImportError:
        return None


_msgpack = _try_msgpack()

try:
    from embers._native import decode_record as _native_decode_record
    from embers._native import encode_record as _native_encode_record
    STORAGE_FORMAT_BACKEND = "rust-rmpv"
except ImportError:
    _native_decode_record = None
    _native_encode_record = None
    STORAGE_FORMAT_BACKEND = "python-msgpack" if _msgpack else "python-json"


def encode(data: dict) -> bytes:
    """Serialize a dict to bytes for storage."""
    if _native_encode_record is not None:
        return bytes(_native_encode_record(data))
    if _msgpack:
        return _msgpack.packb(data, use_bin_type=True)
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def decode(raw: bytes) -> dict:
    """Deserialize bytes back to a dict."""
    if _native_decode_record is not None:
        value = _native_decode_record(raw)
        if not isinstance(value, dict):
            raise ValueError("Ember record payload must decode to a dictionary")
        return value
    if _msgpack:
        return _msgpack.unpackb(raw, raw=False)
    return json.loads(raw.decode("utf-8"))


def encode_index(data: dict) -> bytes:
    """Serialize an index structure. Always JSON for human inspectability."""
    return json.dumps(data, ensure_ascii=False, indent=None).encode("utf-8")


def decode_index(raw: bytes) -> dict:
    """Deserialize an index structure."""
    return json.loads(raw.decode("utf-8"))


def is_msgpack_available() -> bool:
    return _native_encode_record is not None or _msgpack is not None


def backend_name() -> str:
    return "msgpack" if _msgpack else "json"
