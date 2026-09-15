"""
Ember's Diaries — Write-Ahead Log (WAL)
Guarantees crash safety. Before any record is written to the store,
it is logged here first. On startup, incomplete writes are recovered.

Protocol:
  1. Write WAL entry (PENDING)
  2. Write record to store
  3. Mark WAL entry COMMITTED
  4. On crash recovery: any PENDING entries are replayed
"""

import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..storage.format import encode_index, decode_index


try:
    from embers._native import (
        append_wal_commit as _append_wal_commit,
        append_wal_pending as _append_wal_pending,
        checkpoint_wal as _checkpoint_wal,
        recover_wal_lines as _recover_wal_lines,
    )
    WAL_BACKEND = "rust-pyo3"
except ImportError:
    WAL_BACKEND = "python-fallback"

    def _append_line(path: str, line: bytes) -> None:
        with open(path, "ab") as wal_file:
            wal_file.write(line + b"\n")
            wal_file.flush()
            os.fsync(wal_file.fileno())

    def _append_wal_pending(
        path: str,
        wal_id: str,
        operation: str,
        record_id: str,
        data_json: bytes,
        timestamp: str,
    ) -> None:
        data = decode_index(data_json)
        _append_line(path, encode_index({
            "wal_id": wal_id,
            "operation": operation,
            "record_id": record_id,
            "data": data,
            "status": "PENDING",
            "timestamp": timestamp,
        }))

    def _append_wal_commit(path: str, wal_id: str, timestamp: str) -> None:
        _append_line(path, encode_index({
            "wal_id": wal_id,
            "status": "COMMITTED",
            "timestamp": timestamp,
        }))

    def _recover_wal_lines(path: str) -> list[bytes]:
        entries: dict[str, bytes] = {}
        committed: set[str] = set()
        try:
            with open(path, "rb") as wal_file:
                for raw_line in wal_file:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        entry = decode_index(line)
                        wal_id = entry.get("wal_id", "")
                        if entry.get("status") == "COMMITTED":
                            committed.add(wal_id)
                        elif entry.get("status") == "PENDING":
                            entries[wal_id] = line
                    except Exception:
                        continue
        except FileNotFoundError:
            return []
        return [line for wal_id, line in entries.items()
                if wal_id not in committed]

    def _checkpoint_wal(path: str) -> int:
        pending = _recover_wal_lines(path)
        with open(path, "wb") as wal_file:
            for line in pending:
                wal_file.write(line + b"\n")
            wal_file.flush()
            os.fsync(wal_file.fileno())
        return len(pending)


_WAL_FILENAME = "wal.jsonl"


class WALEntry:
    def __init__(self, operation: str, record_id: str, data: dict):
        self.wal_id    = str(uuid.uuid4())
        self.operation = operation   # "write" | "deprecate" | "annotate"
        self.record_id = record_id
        self.data      = data
        self.status    = "PENDING"   # PENDING | COMMITTED
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "wal_id":    self.wal_id,
            "operation": self.operation,
            "record_id": self.record_id,
            "data":      self.data,
            "status":    self.status,
            "timestamp": self.timestamp,
        }


class WriteAheadLog:
    """
    Append-only WAL file. One JSON line per entry.
    Thread-safe via lock.
    """

    def __init__(self, store_path: Path):
        self.path = store_path / _WAL_FILENAME
        self._lock = threading.Lock()
        self._ensure_file()

    def _ensure_file(self):
        if not self.path.exists():
            self.path.touch()

    def log(self, operation: str, record_id: str, data: dict) -> WALEntry:
        """Write a PENDING entry to the WAL. Returns the entry."""
        entry = WALEntry(operation, record_id, data)
        data_json = encode_index(data)
        with self._lock:
            _append_wal_pending(
                str(self.path),
                entry.wal_id,
                entry.operation,
                entry.record_id,
                data_json,
                entry.timestamp,
            )
        return entry

    def commit(self, wal_id: str):
        """
        Mark a WAL entry as COMMITTED.
        We do this by appending a commit marker — the WAL is never modified.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._lock:
            _append_wal_commit(str(self.path), wal_id, timestamp)

    def recover(self) -> list[dict]:
        """
        On startup: scan WAL for PENDING entries with no COMMIT marker.
        Returns list of entries that need to be replayed.
        """
        with self._lock:
            lines = _recover_wal_lines(str(self.path))
        pending = []
        for line in lines:
            try:
                pending.append(decode_index(line))
            except Exception:
                continue
        return pending

    def checkpoint(self):
        """
        Compact the WAL by removing all committed entries.
        Only keeps entries that are still PENDING (should be none in normal operation).
        Safe to call periodically.
        """
        with self._lock:
            _checkpoint_wal(str(self.path))

    def size_bytes(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0
