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


class PhysicalStore:
    """
    Low-level storage engine.
    Handles raw read/write of EmberRecord files.
    Does NOT know about indexes — that's the writer/reader's job.
    """

    def __init__(self, store_path: str | Path):
        self.root = Path(store_path)
        self.records_dir = self.root / "records"
        self.meta_dir    = self.root / "meta"

        self._lock = threading.RLock()
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

    def _write_record_file(self, record: EmberRecord):
        """Write a single record to its UUID.ember file. Never overwrites."""
        record_file = self.records_dir / f"{record.id}.ember"
        if record_file.exists():
            # Record already exists — this is a recovery replay, skip
            return
        raw = encode(record.to_dict())
        # Atomic write: write to temp file, then rename
        tmp_file = self.records_dir / f"{record.id}.tmp"
        try:
            with open(tmp_file, "wb") as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
            tmp_file.rename(record_file)
        except Exception:
            if tmp_file.exists():
                tmp_file.unlink()
            raise

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
