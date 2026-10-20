from embers._native import atomic_replace as _quota_atomic_replace
"""
Ember's Diaries — Timeline Index
Time-series index for fast time-range queries.
Uses sorted lists with binary search for O(log n) lookups.
"""

import bisect
import threading
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from ..storage.format import encode_index, decode_index


class TimelineIndex:
    """
    Per-namespace sorted timeline of (timestamp, record_id) pairs.
    Supports range queries, point-in-time queries, and windowed access.
    """

    def __init__(self, store_path: Path):
        self._path = store_path / "indexes" / "timeline"
        self._path.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

        # namespace → sorted list of (iso_timestamp, record_id)
        self._timelines: dict[str, list[tuple[str, str]]] = defaultdict(list)

        self._load()

    def _load(self):
        index_file = self._path / "timelines.json"
        if not index_file.exists():
            return
        try:
            data = decode_index(index_file.read_bytes())
            for ns, entries in data.get("timelines", {}).items():
                self._timelines[ns] = [(e[0], e[1]) for e in entries]
        except Exception as e:
            print(f"[TimelineIndex] Failed to load: {e}")

    def persist(self):
        with self._lock:
            data = {
                "timelines": {
                    ns: [[ts, rid] for ts, rid in entries]
                    for ns, entries in self._timelines.items()
                }
            }
            index_file = self._path / "timelines.json"
            _quota_atomic_replace(str(index_file), encode_index(data))

    # ── Index operations ──────────────────────────────────────────────────────

    def add(self, record_id: str, namespace: str, timestamp: str):
        """Insert a record into the timeline. Maintains sorted order."""
        with self._lock:
            timeline = self._timelines[namespace]
            entry = (timestamp, record_id)
            # Binary insert to maintain sorted order
            bisect.insort(timeline, entry)

    # ── Queries ───────────────────────────────────────────────────────────────

    def range_query(self, namespace: str,
                    start: datetime | str | None = None,
                    end: datetime | str | None = None) -> list[str]:
        """
        Get record IDs in a time range, ordered by timestamp.
        """
        timeline = self._timelines.get(namespace, [])
        if not timeline:
            return []

        start_iso = start.isoformat() if isinstance(start, datetime) else (start or "")
        end_iso = end.isoformat() if isinstance(end, datetime) else (end or "9999-12-31T23:59:59")

        # Binary search for start position
        lo = bisect.bisect_left(timeline, (start_iso,))
        hi = bisect.bisect_right(timeline, (end_iso + "z",))  # 'z' > any iso char

        return [rid for _, rid in timeline[lo:hi]]

    def before(self, namespace: str, timestamp: datetime | str,
               limit: int = 10) -> list[str]:
        """Get the N most recent records before a timestamp."""
        timeline = self._timelines.get(namespace, [])
        ts = timestamp.isoformat() if isinstance(timestamp, datetime) else timestamp
        idx = bisect.bisect_left(timeline, (ts,))
        start = max(0, idx - limit)
        return [rid for _, rid in timeline[start:idx]]

    def after(self, namespace: str, timestamp: datetime | str,
              limit: int = 10) -> list[str]:
        """Get the N records after a timestamp."""
        timeline = self._timelines.get(namespace, [])
        ts = timestamp.isoformat() if isinstance(timestamp, datetime) else timestamp
        idx = bisect.bisect_right(timeline, (ts + "z",))
        return [rid for _, rid in timeline[idx:idx + limit]]

    def latest(self, namespace: str, limit: int = 10) -> list[str]:
        """Get the N most recent records."""
        timeline = self._timelines.get(namespace, [])
        return [rid for _, rid in timeline[-limit:]]

    def oldest(self, namespace: str, limit: int = 10) -> list[str]:
        """Get the N oldest records."""
        timeline = self._timelines.get(namespace, [])
        return [rid for _, rid in timeline[:limit]]

    def count(self, namespace: str) -> int:
        return len(self._timelines.get(namespace, []))

    def namespaces(self) -> list[str]:
        return list(self._timelines.keys())

    def stats(self) -> dict:
        return {
            "namespaces": len(self._timelines),
            "total_entries": sum(len(v) for v in self._timelines.values()),
        }
