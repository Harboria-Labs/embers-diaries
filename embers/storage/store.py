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
import json
import random
import threading
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

from ..storage.format import encode, decode
from ..engine.wal import WriteAheadLog
from ..core.record import EmberRecord
from ..core.integrity import RecordIntegrityError
from ..core.errors import BatchQuotaExceeded, StorageLimitError


try:
    from embers._native import (
        acquire_store_lock as _acquire_store_file_lock,
        atomic_replace as _atomic_replace,
        atomic_write_new as _atomic_write_new,
        recover_record_transactions as _recover_record_transactions,
        write_record_transaction as _write_record_transaction,
        configure_store_quota as _configure_store_quota,
        store_byte_usage as _store_byte_usage,
    )
    import embers._native as _native
    # A binary built before write_record_batch must keep the rest of the
    # native backend. Grouped commits then fall back to one transaction each.
    _write_record_batch = getattr(_native, "write_record_batch", None)
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

    def _atomic_replace(path: str, data: bytes) -> None:
        destination = Path(path)
        temp = destination.with_suffix(destination.suffix + ".tmp")
        try:
            with open(temp, "wb") as temp_file:
                temp_file.write(data)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            temp.replace(destination)
        except Exception:
            if temp.exists():
                temp.unlink()
            raise

    _recover_record_transactions = None
    _write_record_transaction = None
    _write_record_batch = None

try:
    from embers._native import source_epoch_protocol_version as _source_epoch_version
    NATIVE_SOURCE_EPOCH = _source_epoch_version() >= 1
except ImportError:
    # The installed extension may predate this source change. Keep its writes
    # safe through the Python storage wrapper without replacing that binary.
    NATIVE_SOURCE_EPOCH = False


_WRITE_GROUP: ContextVar = ContextVar("ember_write_group", default=None)


def current_write_group():
    """The staged write batch for this thread, if one is open."""
    return _WRITE_GROUP.get()


def bind_write_group(group):
    return _WRITE_GROUP.set(group)


def reset_write_group(token) -> None:
    _WRITE_GROUP.reset(token)


class WriteGroup:
    """Records accepted into one grouped commit, not yet in the WAL."""

    def __init__(self):
        self.records: list[EmberRecord] = []
        self.raws: list[bytes] = []
        self.dicts: list[dict] = []
        self.by_id: dict[str, EmberRecord] = {}
        self.staged_bytes = 0
        self.aborted = False
        self.committed = False

    def add(self, record: EmberRecord, raw: bytes, data: dict) -> None:
        self.records.append(record)
        self.raws.append(raw)
        self.dicts.append(data)
        self.by_id[record.id] = record
        self.staged_bytes += len(raw)

    def truncate(self, length: int) -> list[EmberRecord]:
        removed = self.records[length:]
        self.records = self.records[:length]
        self.raws = self.raws[:length]
        self.dicts = self.dicts[:length]
        self.by_id = {record.id: record for record in self.records}
        self.staged_bytes = sum(len(raw) for raw in self.raws)
        return removed


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

    def __init__(self, store_path: str | Path, *, max_store_bytes: int = 0,
                 max_record_bytes: int = 0, max_total_bytes: int = 0):
        self.root = Path(store_path)
        self.max_store_bytes = max_store_bytes
        self.max_record_bytes = max_record_bytes
        self.root.mkdir(parents=True, exist_ok=True)
        self.records_dir = self.root / "records"
        self.meta_dir    = self.root / "meta"
        # (ids, directory mtime_ns). The mtime is a stamp we set, not the
        # kernel's coarse wall clock: a create or unlink moves it off the
        # stamp, so a deleted file cannot stay in the cache.
        self._id_cache = None
        self._id_scans = 0

        # EmberDB.connect() can be called more than once for the same store.
        # A per-instance lock protects only one handle, so two handles could
        # interleave WAL, record, and sidecar operations. Share one lock for
        # every handle in this process that points at the same store.
        key = os.path.normcase(str(self.root.resolve()))
        with self._shared_locks_guard:
            self._lock = self._shared_locks.setdefault(
                key, _StoreTransactionLock(self.root / ".ember.lock"))
        with self._lock:
            if type(max_total_bytes) is not int or max_total_bytes < 0:
                raise ValueError("max_total_bytes must be a nonnegative integer")
            if max_total_bytes:
                if STORE_LOCK_BACKEND != "rust-pyo3":
                    raise RuntimeError("total-store quota requires native storage")
                _configure_store_quota(str(self.root), max_total_bytes)
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
        epoch_file = self.meta_dir / "source_epoch.json"
        if not epoch_file.exists():
            _atomic_replace(str(epoch_file), b'{"epoch":0}')

    def _recover(self):
        """Replay any uncommitted WAL entries on startup."""
        # A recovery commit is a source change too. Invalidate candidates
        # before invoking native recovery so a crash during replay cannot
        # leave a new record at the old indexed epoch.
        pending = self.wal.recover()
        if pending and not (NATIVE_SOURCE_EPOCH and
                            _recover_record_transactions is not None):
            self.begin_index_change()
        if _recover_record_transactions is not None:
            report = json.loads(bytes(_recover_record_transactions(
                str(self.root), datetime.now(timezone.utc).isoformat())))
            recovered = report.get("recovered", [])
            if recovered:
                print(f"[EmberStore] Recovered {len(recovered)} uncommitted WAL entries.")
            if report.get("failed"):
                raise RuntimeError("Native WAL recovery failed: " +
                                   "; ".join(report["failed"]))
            return
        if pending:
            print(f"[EmberStore] Recovering {len(pending)} uncommitted WAL entries...")
            for entry in pending:
                if entry.get("operation") == "write":
                    try:
                        record = EmberRecord.from_dict(entry["data"])
                        self._write_record_file(record)
                        self.wal.commit(entry["wal_id"])
                    except Exception as e:
                        raise RuntimeError(
                            f"WAL recovery failed for {entry.get('record_id')}: {e}") from e

    # ── Write ─────────────────────────────────────────────────────────────────

    def write(self, record: EmberRecord) -> str:
        """
        Write a record to disk. Atomic + crash-safe via WAL.
        Returns the record ID.
        """
        with self._lock:
            record_dict = record.to_dict()
            raw = encode(record_dict)
            group = current_write_group()
            extra = group.staged_bytes if group is not None else 0
            self._check_write_limits(len(raw), extra_bytes=extra)
            if group is not None:
                # Durability waits for commit_staged. Snapshot the sealed bytes
                # now so a later in-memory read overlay cannot change them.
                if record.id in group.by_id or self.file_exists(record.id):
                    raise ValueError(
                        f"Record {record.id} already exists. "
                        "Ember's Diaries is append-only — use update() to create a new version."
                    )
                group.add(record, raw, record_dict)
                return record.id
            # Publish the invalidation before the native WAL transaction.  If
            # the transaction is interrupted, the epoch can be ahead, never
            # falsely equal to an index missing an acknowledged record.
            if not (NATIVE_SOURCE_EPOCH and _write_record_transaction is not None):
                self.begin_index_change()

            if _write_record_transaction is not None:
                now = datetime.now(timezone.utc).isoformat()
                from ..storage.format import encode_index
                meta = self._read_meta()
                meta["record_count"] = meta.get("record_count", 0) + 1
                meta["last_write"] = now
                _write_record_transaction(
                    str(self.root), str(uuid.uuid4()), now,
                    datetime.now(timezone.utc).isoformat(), record.id,
                    json.dumps(record_dict, ensure_ascii=False,
                               separators=(",", ":")).encode("utf-8"),
                    raw, encode_index(meta))
                self._note_record_ids([record.id])
                return record.id

            # Step 1: Log to WAL (PENDING)
            wal_entry = self.wal.log("write", record.id, record_dict)

            # Step 2: Write the record file
            self._write_record_file(record, raw)

            # Step 3: Mark WAL entry as COMMITTED
            self.wal.commit(wal_entry.wal_id)

            # Step 4: Update record count in meta
            self._increment_record_count()
            self._note_record_ids([record.id])

            return record.id

    def file_exists(self, record_id: str) -> bool:
        """True only for a record file, not a staged batch overlay."""
        return (self.records_dir / f"{record_id}.ember").exists()

    def commit_staged(self, group: WriteGroup) -> None:
        """Durably commit every staged record in one native transaction.

        Pending WAL lines share one fsync, commit lines share one fsync, and
        store metadata is replaced once. Record files are still synced one by
        one so a commit line never acknowledges bytes that are not on disk.
        When the installed extension has no batch symbol, each record uses the
        ordinary single-write transaction instead.
        """
        if not group.records:
            return
        if _write_record_batch is None:
            self._commit_staged_individually(group)
            return
        now = datetime.now(timezone.utc).isoformat()
        wal_ids = [str(uuid.uuid4()) for _ in group.records]
        record_ids = [record.id for record in group.records]
        data_jsons = [
            json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            for data in group.dicts
        ]
        from ..storage.format import encode_index
        meta = self._read_meta()
        meta["record_count"] = meta.get("record_count", 0) + len(group.records)
        meta["last_write"] = now
        try:
            _write_record_batch(
                str(self.root), wal_ids, now, now, record_ids,
                data_jsons, list(group.raws), encode_index(meta))
            self._note_record_ids(record_ids)
        except Exception as exc:
            # Admission runs before the epoch moves. A quota refusal with no
            # record files is safe to retry one record at a time. Any file
            # that already exists stays for WAL recovery; do not rewrite it.
            if "max_total_bytes" in str(exc) and not any(
                    self.file_exists(record.id) for record in group.records):
                raise BatchQuotaExceeded(group.records, str(exc)) from exc
            raise

    def _commit_staged_individually(self, group: WriteGroup) -> None:
        """One durable transaction per staged record, used without the batch symbol.

        The group context is cleared so ``write`` cannot stage the record again.
        Callers keep deferred callbacks for records that land on disk.
        """
        token = bind_write_group(None)
        pending = list(group.records)
        try:
            for index, record in enumerate(pending):
                try:
                    self.write(record)
                except Exception as exc:
                    if "max_total_bytes" in str(exc):
                        raise BatchQuotaExceeded(pending[index:], str(exc)) from exc
                    raise
        finally:
            reset_write_group(token)

    def index_epoch(self) -> int:
        """Conservative source-change epoch; missing metadata fails closed."""
        path = self.meta_dir / "source_epoch.json"
        data = json.loads(path.read_bytes())
        epoch = data.get("epoch")
        if type(epoch) is not int or epoch < 0:
            raise ValueError("Invalid source index epoch")
        return epoch

    def begin_index_change(self) -> int:
        """Invalidate derived candidates before a record or visibility sidecar changes.

        Callers hold the shared store lock. The native atomic replacement is
        durable before the source mutation; an abandoned epoch only causes a
        conservative rebuild.
        """
        with self._lock:
            epoch = self.index_epoch() + 1
            _atomic_replace(str(self.meta_dir / "source_epoch.json"),
                            json.dumps({"epoch": epoch}).encode("ascii"))
            return epoch

    def read_strict(self, record_id: str) -> EmberRecord:
        """Read a known physical record for index certification, failing closed."""
        path = self.records_dir / f"{record_id}.ember"
        raw = path.read_bytes()
        record = EmberRecord.from_dict(decode(raw))
        if record.id != record_id:
            raise ValueError(f"Record identity mismatch for {record_id}")
        if record.content_hash is not None:
            record.verify_integrity()
        return record

    @property
    def lock(self):
        """The process-local lock shared by all handles for this store."""
        return self._lock

    def _write_record_file(self, record: EmberRecord, raw: bytes | None = None):
        """Write a single record to its UUID.ember file. Never overwrites."""
        record_file = self.records_dir / f"{record.id}.ember"
        raw = raw if raw is not None else encode(record.to_dict())
        # Rust publishes the fully synced temp file without ever overwriting an
        # existing immutable record. False means recovery found it already
        # present, which is the expected idempotent replay case.
        _atomic_write_new(str(record_file), raw)

    def _check_write_limits(self, encoded_size: int, extra_bytes: int = 0) -> None:
        if self.max_record_bytes and encoded_size > self.max_record_bytes:
            raise StorageLimitError(
                "storage.max_record_bytes", self.max_record_bytes, encoded_size)
        if self.max_store_bytes:
            current = sum(
                path.stat().st_size for path in self.records_dir.glob("*.ember"))
            attempted = current + extra_bytes + encoded_size
            if attempted > self.max_store_bytes:
                raise StorageLimitError(
                    "storage.max_store_bytes", self.max_store_bytes, attempted)

    # ── Read ──────────────────────────────────────────────────────────────────

    def read(self, record_id: str) -> EmberRecord | None:
        """Read a record by ID. Returns None if not found."""
        record_file = self.records_dir / f"{record_id}.ember"
        if not record_file.exists():
            group = current_write_group()
            if group is not None:
                return group.by_id.get(record_id)
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
        if self.file_exists(record_id):
            return True
        group = current_write_group()
        return group is not None and record_id in group.by_id

    def all_ids(self) -> list[str]:
        """Return all record IDs in the store."""
        with self._lock:
            return list(self._all_ids_locked())

    def _all_ids_locked(self) -> list[str]:
        try:
            mtime = self.records_dir.stat().st_mtime_ns
        except OSError:
            self._id_cache = None
            return [
                f.stem for f in self.records_dir.iterdir()
                if f.suffix == ".ember"
            ]
        cached = self._id_cache
        if cached is not None and cached[1] == mtime:
            return cached[0]
        self._id_scans += 1
        ids = [
            f.stem for f in self.records_dir.iterdir()
            if f.suffix == ".ember"
        ]
        # A file created during the scan changes mtime; don't publish a
        # partial list under the new timestamp.
        try:
            mtime_after = self.records_dir.stat().st_mtime_ns
        except OSError:
            self._id_cache = None
            return ids
        if mtime_after == mtime:
            self._publish_id_cache(ids)
        return ids

    def _publish_id_cache(self, ids: list[str]) -> None:
        """Remember ids under a directory mtime only this process would set.

        Wall-clock directory mtime on this filesystem stays still across a
        burst of creates and deletes, so a cache keyed by that clock kept
        serving an id after its file was removed. An explicit stamp changes
        on the next create or unlink, which drops the cache.
        """
        stamp = random.getrandbits(63) or 1
        try:
            os.utime(self.records_dir, ns=(stamp, stamp))
            got = self.records_dir.stat().st_mtime_ns
        except OSError:
            self._id_cache = None
            return
        self._id_cache = (ids, stamp) if got == stamp else None

    def _note_record_ids(self, record_ids: list[str]) -> None:
        """Keep the id cache aligned with files this process just created."""
        cached = self._id_cache
        if cached is None:
            return
        known = cached[0]
        for record_id in record_ids:
            if record_id not in known:
                known.append(record_id)
        self._publish_id_cache(known)

    def record_count(self) -> int:
        return sum(1 for f in self.records_dir.iterdir() if f.suffix == ".ember")

    # ── Meta ──────────────────────────────────────────────────────────────────

    def _write_meta(self, meta: dict):
        from ..storage.format import encode_index
        meta_file = self.meta_dir / "store.json"
        _atomic_replace(str(meta_file), encode_index(meta))

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

    def byte_usage(self) -> dict:
        if STORE_LOCK_BACKEND != "rust-pyo3":
            raise RuntimeError("native storage usage unavailable")
        with self._lock:
            used, limit = _store_byte_usage(str(self.root))
            return {"managed_logical_bytes": used, "max_total_bytes": limit,
                    "remaining_bytes": None if limit is None else max(0, limit - used),
                    "unit": "bytes", "includes": "all files below store root",
                    "excludes": "filesystem allocation overhead, RAM and external paths"}

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
