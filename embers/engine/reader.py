"""
Ember's Diaries — Read Engine
Handles all read operations. Aware of supersession and deprecation.
"""

from datetime import datetime
from ..core.record import EmberRecord
from ..storage.store import PhysicalStore
from ..index.master import MasterIndex


class ReadEngine:
    """
    Read layer. Sits above PhysicalStore.
    Handles supersession resolution, deprecation filtering, access tracking.
    """

    def __init__(self, store: PhysicalStore, writer=None, master_index: MasterIndex | None = None):
        self._store  = store
        self._writer = writer  # WriteEngine ref for supersession checks
        self._master_index = master_index

    # ── Primary read ──────────────────────────────────────────────────────────

    def get(self, record_id: str,
            include_deprecated: bool = False,
            include_superseded: bool = False,
            track_access: bool = True) -> EmberRecord | None:
        """
        Get a record by ID.
        By default: returns None for deprecated or superseded records.
        """
        record = self._store.read(record_id)
        if record is None:
            return None

        # Check deprecation via sidecar
        if self._writer and self._writer.is_deprecated(record_id):
            if not include_deprecated:
                return None
            record.deprecated = True

        # Check supersession via sidecar
        if self._writer:
            superseded_by = self._writer.get_superseded_by(record_id)
            if superseded_by and not include_superseded:
                return None
            if superseded_by:
                record.superseded_by = superseded_by

        # Attach annotations
        if self._writer:
            record.annotations = self._writer.get_annotations(record_id)

        return record

    def get_current(self, record_id: str) -> EmberRecord | None:
        """
        Follow the supersession chain and return the current (head) record.
        """
        if self._writer is None:
            return self.get(record_id)

        chain = self._writer.get_supersession_chain(record_id)
        head_id = chain[-1]
        return self.get(head_id, include_superseded=False)

    def get_history(self, record_id: str) -> list[EmberRecord]:
        """
        Return the full supersession chain as records, oldest → newest.
        """
        if self._writer is None:
            r = self.get(record_id, include_superseded=True)
            return [r] if r else []

        chain = self._writer.get_supersession_chain(record_id)
        records = []
        for rid in chain:
            r = self.get(rid, include_deprecated=True, include_superseded=True)
            if r:
                records.append(r)
        return records

    def get_at(self, record_id: str, timestamp: datetime) -> EmberRecord | None:
        """
        Return the state of a record at a specific point in time.
        Finds the version that was current at that timestamp.
        """
        history = self.get_history(record_id)
        if not history:
            return None

        # Find the last record created before or at the timestamp
        valid = [r for r in history if r.created_at <= timestamp]
        if not valid:
            return None
        return valid[-1]

    # ── Bulk reads ────────────────────────────────────────────────────────────

    def get_many(self, record_ids: list[str],
                 include_deprecated: bool = False,
                 include_superseded: bool = False) -> list[EmberRecord]:
        """Batch get — skips missing records silently."""
        results = []
        for rid in record_ids:
            r = self.get(rid, include_deprecated, include_superseded)
            if r is not None:
                results.append(r)
        return results

    def get_namespace(self, namespace: str,
                      include_deprecated: bool = False,
                      include_superseded: bool = False,
                      limit: int | None = None) -> list[EmberRecord]:
        """
        Return all records in a namespace.

        Resolve the namespace to record IDs through the shared MasterIndex,
        then read only those records. This avoids scanning every .ember file.
        The fallback is retained only for standalone ReadEngine instances that
        have no EmberDB-created MasterIndex available yet.
        """
        master_index = self._master_index or MasterIndex.get_registered(self._store.root)
        if master_index is not None:
            self._master_index = master_index
            record_ids = master_index.get_namespace_ids(
                namespace,
                include_deprecated=include_deprecated,
                include_superseded=include_superseded,
            )
            if limit is not None:
                record_ids = record_ids[:limit]
            return self.get_many(
                record_ids,
                include_deprecated=include_deprecated,
                include_superseded=include_superseded,
            )

        # Compatibility fallback for standalone ReadEngine usage.
        all_ids = self._store.all_ids()
        results = []
        for record_id in all_ids:
            r = self.get(record_id, include_deprecated, include_superseded)
            if r and r.namespace == namespace:
                results.append(r)
                if limit and len(results) >= limit:
                    break
        return results

    def get_all(self, include_deprecated: bool = False,
                include_superseded: bool = False) -> list[EmberRecord]:
        """Return every record in the store. Use with caution on large stores."""
        all_ids = self._store.all_ids()
        return self.get_many(all_ids, include_deprecated, include_superseded)

    def exists(self, record_id: str) -> bool:
        return self._store.exists(record_id)

    def count(self, namespace: str | None = None) -> int:
        if namespace is None:
            return self._store.record_count()
        return len(self.get_namespace(namespace))
