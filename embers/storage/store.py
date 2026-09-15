"""
Ember's Diaries — Physical Storage Engine
One file per record. Atomic writes. Crash-safe. Thread-safe.
Records are written once and never modified.

Directory layout:
  store_path/
  ├── records/          ← one UUID.ember file per record
  ├── meta/             ← store metadata
  └── wal.jsonl         ← write-ahead log
"""

import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from ..storage.format import encode, decode
from ..engine.wal import WriteAheadLog
from ..core.record import EmberRecord
from ..core.integrity import RecordIntegrityError


try:
    from embers._native import (
        acquire_store_lock as _acquire_store_file_lock,
        atomic_write_new as _atomic_write_new,
    )
    STORE_LOCK_BACKEND = "rust-pyo3"
except ImportError:
    STORE_LOCK_BACKEND = "python-process-local"

    class _FallbackStoreFileLock:
        def release(self):
            return None

    def _acquire_store_file_lock(path: str):
        return _FallbackStoreFileLock()

    def _atomic_write_new(path: str, data: bytes) -> bool:
        destination = Path(path)
        if destination.exists():
            return False
        temp = destination.with_suffix(".tmp")
        try:
            with open(temp, "wb") as temp_file:
                temp_file.write(data)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            temp.rename(destination)
            return True
        except Exception:
            if temp.exists():
                temp.unlink()
            raise


class _StoreTransactionLock:
    """Reentrant thread lock plus one Rust-owned inter-process file lock."""

    def __init__(self, lock_path: Path):
        self._thread_lock = threading.RLock()
        self._local = threading.local()
        self._lock_path = lock_path
        self._native_guard = None

    def __enter__(self):
        self._thread_lock.acquire()
        depth = getattr(self._local, "depth", 0)
        try:
            if depth == 0:
                self._native_guard = _acquire_store_file_lock(
                    str(self._lock_path))
            self._local.depth = depth + 1
            return self
        except Exception:
            self._thread_lock.release()
            raise

    def __exit__(self, exc_type, exc_value, traceback):
        depth = self._local.depth - 1
        self._local.depth = depth
        try:
            if depth == 0:
                guard, self._native_guard = self._native_guard, None
                guard.release()
        finally:
            self._thread_lock.release()


class PhysicalStore:
    """
    Low-level storage engine.
    Handles raw read/write of EmberRecord files.
    Does NOT know about indexes — that's the writer/reader's job.
    """

    _shared_locks: dict[str, _StoreTransactionLock] = {}
    _shared_locks_guard = threading.RLock()

    def __init__(self, store_path: str | Path):
        self.root = Path(store_path)
        self.root.mkdir(parents=True, exist_ok=True)
        self.records_dir = self.root / "records"
        self.meta_dir    = self.root / "meta"

        # EmberDB.connect() can be called more than once for the same store.
        # A per-instance lock protects only one handle, so two handles could
        # interleave WAL, record, and sidecar operations. Share one lock for
        # every handle in this process that points at the same store.
        key = os.path.normcase(str(self.root.resolve()))
        with self._shared_locks_guard:
            self._lock = self._shared_locks.setdefault(
                key, _StoreTransactionLock(self.root / ".ember.lock"))
        with self._lock:
            self._setup()
            self.wal = WriteAheadLog(self.root)
            self._recover()

    def _setup(self):
        """Create directory structure if it doesn't exist."""
        self.records_dir.mkdir(parents=True, exist_ok=True)
        self.meta_dir.mkdir(parents=True, exist_ok=True)

        # Write store metadata on first init
        meta_file = self.meta_dir / "store.json"
        if not meta_file.exists():
            self._write_meta({
                "version":    "1.0",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "engine":     "ember_diaries",
                "record_count": 0,
            })

    def _recover(self):
        """Replay any uncommitted WAL entries on startup."""
        pending = self.wal.recover()
        if pending:
            print(f"[EmberStore] Recovering {len(pending)} uncommitted WAL entries...")
            for entry in pending:
                if entry.get("operation") == "write":
                    try:
                        record = EmberRecord.from_dict(entry["data"])
                        self._write_record_file(record)
                        self.wal.commit(entry["wal_id"])
                    except Exception as e:
                        print(f"[EmberStore] Recovery failed for {entry.get('record_id')}: {e}")

    # ── Write ─────────────────────────────────────────────────────────────────

    def write(self, record: EmberRecord) -> str:
        """
        Write a record to disk. Atomic + crash-safe via WAL.
        Returns the record ID.
        """
        with self._lock:
            record_dict = record.to_dict()

            # Step 1: Log to WAL (PENDING)
            wal_entry = self.wal.log("write", record.id, record_dict)

            # Step 2: Write the record file
            self._write_record_file(record)

            # Step 3: Mark WAL entry as COMMITTED
            self.wal.commit(wal_entry.wal_id)

            # Step 4: Update record count in meta
            self._increment_record_count()

            return record.id

    @property
    def lock(self):
        """The process-local lock shared by all handles for this store."""
        return self._lock

    def _write_record_file(self, record: EmberRecord):
        """Write a single record to its UUID.ember file. Never overwrites."""
        record_file = self.records_dir / f"{record.id}.ember"
        raw = encode(record.to_dict())
        # Rust publishes the fully synced temp file without ever overwriting an
        # existing immutable record. False means recovery found it already
        # present, which is the expected idempotent replay case.
        _atomic_write_new(str(record_file), raw)

    # ── Read ──────────────────────────────────────────────────────────────────

    def read(self, record_id: str) -> EmberRecord | None:
        """Read a record by ID. Returns None if not found."""
        record_file = self.records_dir / f"{record_id}.ember"
        if not record_file.exists():
            return None
        try:
            raw = record_file.read_bytes()
            record = EmberRecord.from_dict(decode(raw))
            if record.content_hash is not None:
                record.verify_integrity()
            return record
        except RecordIntegrityError:
            raise
        except Exception as e:
            print(f"[EmberStore] Failed to read {record_id}: {e}")
            return None

    def exists(self, record_id: str) -> bool:
        return (self.records_dir / f"{record_id}.ember").exists()

    def all_ids(self) -> list[str]:
        """Return all record IDs in the store."""
        return [
            f.stem for f in self.records_dir.iterdir()
            if f.suffix == ".ember"
        ]

    def record_count(self) -> int:
        return sum(1 for f in self.records_dir.iterdir() if f.suffix == ".ember")

    # ── Meta ──────────────────────────────────────────────────────────────────

    def _write_meta(self, meta: dict):
        from ..storage.format import encode_index
        meta_file = self.meta_dir / "store.json"
        meta_file.write_bytes(encode_index(meta))

    def _read_meta(self) -> dict:
        from ..storage.format import decode_index
        meta_file = self.meta_dir / "store.json"
        if not meta_file.exists():
            return {}
        return decode_index(meta_file.read_bytes())

    def _increment_record_count(self):
        meta = self._read_meta()
        meta["record_count"] = meta.get("record_count", 0) + 1
        meta["last_write"] = datetime.now(timezone.utc).isoformat()
        self._write_meta(meta)

    def stats(self) -> dict:
        meta = self._read_meta()
        actual_count = self.record_count()
        return {
            "store_path":    str(self.root),
            "record_count":  actual_count,
            "wal_size_bytes": self.wal.size_bytes(),
            "meta":          meta,
        }

    def checkpoint_wal(self):
        """Compact the WAL. Safe to call periodically."""
        self.wal.checkpoint()
