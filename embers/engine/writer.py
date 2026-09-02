"""
Ember's Diaries — Write Engine
Handles all write operations: new records, supersession, deprecation, annotation.
Enforces append-only semantics — nothing is ever destroyed.
"""

import threading
from datetime import datetime, timezone

from ..core.record import EmberRecord
from ..core.annotation import Annotation
from ..core.types import DeprecationReason
from ..core.errors import ConcurrentModificationError
from ..storage.store import PhysicalStore


class WriteEngine:
    """
    The single point of entry for all writes.
    Enforces: append-only, WAL-protected, thread-safe.
    """

    def __init__(self, store: PhysicalStore):
        self._store = store
        self._lock  = threading.RLock()
        self._write_callbacks: list = []  # notify index layer after writes

    def register_callback(self, fn):
        """Register a function to call after every successful write."""
        self._write_callbacks.append(fn)

    def _notify(self, record: EmberRecord, operation: str):
        for fn in self._write_callbacks:
            try:
                fn(record, operation)
            except Exception as e:
                print(f"[WriteEngine] Callback error: {e}")

    # ── Primary write ─────────────────────────────────────────────────────────

    def write(self, record: EmberRecord) -> str:
        """
        Write a new record to the store.
        Returns the record ID.
        Raises ValueError if a record with this ID already exists.
        """
        with self._lock:
            record.seal()
            if self._store.exists(record.id):
                raise ValueError(
                    f"Record {record.id} already exists. "
                    "Ember's Diaries is append-only — use update() to create a new version."
                )
            record_id = self._store.write(record)
            self._notify(record, "write")
            return record_id

    # ── Update (supersession) ─────────────────────────────────────────────────

    def update(self, old_record_id: str, new_data: dict,
               written_by: str = "system",
               agent_id: str | None = None,
               session_id: str | None = None,
               creation_reason: str | None = None,
               derived_from: list | None = None,
               expected_hash: str | None = None) -> tuple[str, str]:
        """
        Create a new version of an existing record.
        The old record is marked as superseded — never deleted.
        Returns (new_record_id, old_record_id).

        This is how UPDATE works in Ember's Diaries:
          old record → superseded_by = new record id
          new record → supersedes = old record id

        Provenance (Feature #3) describes *this write act*, so it is NOT
        inherited from the old version — a new version may be written by a
        different agent, in a different session, for its own reason. Callers
        that want attribution pass it explicitly; otherwise it stays unset.
        The supersession link (parent_hash / supersedes) already captures the
        lineage independently of derived_from.

        OPTIMISTIC CONCURRENCY (Feature #6). When `expected_hash` is given, it
        is a precondition: the caller asserts it is basing this new version on a
        specific state it read. The check is against the CURRENT HEAD of the
        lineage — if another writer has since committed a newer version, the
        head's hash no longer matches and the write is REFUSED with a structured
        ConcurrentModificationError instead of silently forking a branch. The
        check-and-supersede happens under the write lock, so two racing writers
        are serialized: the first wins and advances the head; the second finds
        its expected_hash stale and conflicts (no silent lost update).

        Omitting `expected_hash` preserves the original behavior, including the
        deliberate branching of Feature #2 (supersede any version, even a
        non-head, to fork the lineage).
        """
        with self._lock:
            # ── Optimistic-concurrency precondition (§6) ─────────────────────
            if expected_hash is not None:
                # Resolve to the current head of this lineage. If old_record_id
                # was already superseded, the head is a later version and its
                # hash cannot match expected_hash → conflict.
                head_id = self.get_supersession_chain(old_record_id)[-1]
                head = self._store.read(head_id)
                if head is None:
                    raise KeyError(f"Record {old_record_id} not found.")
                actual_hash = head.content_hash or head.compute_content_hash()
                if actual_hash != expected_hash:
                    raise ConcurrentModificationError(
                        record_id=old_record_id,
                        expected_hash=expected_hash,
                        actual_hash=actual_hash,
                        current_version=head.version,
                        attempted_version=head.version + 1,
                        current_id=head_id,
                    )
                # Precondition holds: base the new version on the head itself,
                # giving strict linear CAS semantics (never an accidental fork).
                old_record_id = head_id

            old = self._store.read(old_record_id)
            if old is None:
                raise KeyError(f"Record {old_record_id} not found.")
            if old.deprecated:
                raise ValueError(f"Record {old_record_id} is deprecated and cannot be updated.")

            import uuid
            new_id = str(uuid.uuid4())

            # Build new record inheriting from old
            new_record = EmberRecord(
                id          = new_id,
                namespace   = old.namespace,
                record_type = old.record_type,
                data        = new_data,
                version     = old.version + 1,
                parent_hash = old.content_hash or old.compute_content_hash(),
                supersedes  = old_record_id,
                written_by  = written_by,
                agent_id        = agent_id,
                session_id      = session_id,
                creation_reason = creation_reason,
                derived_from    = list(derived_from) if derived_from else [],
                tags        = old.tags.copy(),
                confidence  = old.confidence,
                decay_rate  = old.decay_rate,
                parent_id   = old.parent_id,
                training_candidate  = old.training_candidate,
                retrieval_candidate = old.retrieval_candidate,
            )

            # Seal the new version BEFORE persistence, exactly as a first-class
            # write does. This is essential, not cosmetic:
            #   • Without it the *head* of an updated chain is unsealed
            #     (content_hash is None), so store.read() skips
            #     verify_integrity() and on-disk tampering of the CURRENT
            #     version of a memory — the one recall() returns — goes
            #     undetected. Sealing closes that hole.
            #   • It also makes the version chain cryptographically sound: the
            #     next update binds its parent_hash to this persisted hash
            #     (see the `parent_hash=` line above), which only works if the
            #     parent actually stored a hash of its own.
            new_record.seal()

            # Write the new record first
            self._store.write(new_record)

            # Now mark old record as superseded
            # We do this by writing a NEW record that is the "tombstone" marker
            # The original old record file is NEVER modified
            # Instead we store the supersession in a sidecar index
            self._mark_superseded(old_record_id, new_id)

            self._notify(new_record, "update")
            return new_id, old_record_id

    def _mark_superseded(self, old_id: str, new_id: str):
        """
        Record that old_id has been superseded by new_id.
        We write a sidecar file rather than modifying the original.
        """
        sidecar_dir = self._store.root / "supersessions"
        sidecar_dir.mkdir(exist_ok=True)
        sidecar = sidecar_dir / f"{old_id}.superseded"
        from ..storage.format import encode_index
        sidecar.write_bytes(encode_index({
            "old_id":    old_id,
            "new_id":    new_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }))

    def get_superseded_by(self, record_id: str) -> str | None:
        """Check if a record has been superseded. Returns new_id or None."""
        sidecar = self._store.root / "supersessions" / f"{record_id}.superseded"
        if not sidecar.exists():
            return None
        from ..storage.format import decode_index
        d = decode_index(sidecar.read_bytes())
        return d.get("new_id")

    def get_supersession_chain(self, record_id: str) -> list[str]:
        """Follow the supersession chain from oldest to newest. Returns list of IDs."""
        chain = [record_id]
        current = record_id
        seen = set()
        while True:
            if current in seen:
                break
            seen.add(current)
            next_id = self.get_superseded_by(current)
            if next_id is None:
                break
            chain.append(next_id)
            current = next_id
        return chain

    # ── Deprecation ───────────────────────────────────────────────────────────

    def deprecate(self, record_id: str,
                  reason: DeprecationReason = DeprecationReason.MANUAL,
                  note: str = "",
                  written_by: str = "system") -> bool:
        """
        Mark a record as deprecated.
        The record remains in the store — permanently readable.
        Returns True if successful.
        """
        with self._lock:
            record = self._store.read(record_id)
            if record is None:
                raise KeyError(f"Record {record_id} not found.")
            if self.is_deprecated(record_id):
                return False  # Already deprecated — check sidecar, not the immutable record

            # Write deprecation sidecar — never modify the original
            dep_dir = self._store.root / "deprecations"
            dep_dir.mkdir(exist_ok=True)
            sidecar = dep_dir / f"{record_id}.deprecated"
            from ..storage.format import encode_index
            sidecar.write_bytes(encode_index({
                "record_id":  record_id,
                "reason":     reason.value,
                "note":       note,
                "written_by": written_by,
                "timestamp":  datetime.now(timezone.utc).isoformat(),
            }))

            self._notify(record, "deprecate")
            return True

    def is_deprecated(self, record_id: str) -> bool:
        sidecar = self._store.root / "deprecations" / f"{record_id}.deprecated"
        return sidecar.exists()

    # ── Annotation ────────────────────────────────────────────────────────────

    def annotate(self, record_id: str, annotation: Annotation) -> str:
        """
        Add an annotation to an existing record.
        The original record is NOT modified.
        Annotations are stored in a separate annotations store.
        Returns annotation ID.
        """
        with self._lock:
            if not self._store.exists(record_id):
                raise KeyError(f"Record {record_id} not found.")

            annotation.target_record_id = record_id

            ann_dir = self._store.root / "annotations"
            ann_dir.mkdir(exist_ok=True)

            # Append to per-record annotation file
            ann_file = ann_dir / f"{record_id}.annotations.jsonl"
            from ..storage.format import encode_index
            with open(ann_file, "ab") as f:
                line = encode_index(annotation.to_dict()) + b"\n"
                f.write(line)

            # Notify — pass a stub record for the callback
            record = self._store.read(record_id)
            if record:
                self._notify(record, "annotate")

            return annotation.id

    def get_annotations(self, record_id: str) -> list[Annotation]:
        """Retrieve all annotations for a record."""
        ann_file = self._store.root / "annotations" / f"{record_id}.annotations.jsonl"
        if not ann_file.exists():
            return []
        from ..storage.format import decode_index
        from ..core.annotation import Annotation
        annotations = []
        for line in ann_file.read_bytes().splitlines():
            if line.strip():
                try:
                    annotations.append(Annotation.from_dict(decode_index(line)))
                except Exception:
                    pass
        return annotations
