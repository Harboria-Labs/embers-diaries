"""
Ember's Diaries — Episodic Segmentation Engine
Inspired by EM-LLM (2024): segments streams of records into coherent
episodic events using surprise-based boundary detection.

Human episodic memory organizes experiences into discrete events.
This engine does the same for record streams — grouping related records
into episodes that can be retrieved as coherent units.
"""

import uuid
import math
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from ..core.record import EmberRecord
from ..core.types import RecordType


def _tz_safe(dt: datetime) -> datetime:
    """Same guard as consolidation.py/_tz_safe and decay.py's
    effective_confidence: EmberRecord.created_at defaults to
    datetime.utcnow() (naive); mixing that with an explicitly-aware
    timestamp raises TypeError on comparison. Records module-owns its own
    copy since this file has no dependency on cognitive.consolidation."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


class Episode:
    """
    A coherent group of records forming a single episodic event.
    Episodes are the retrieval unit for episodic memory queries.
    """

    def __init__(self, episode_id: str | None = None):
        self.id = episode_id or str(uuid.uuid4())
        self.record_ids: list[str] = []
        self.start_time: datetime | None = None
        self.end_time: datetime | None = None
        self.tags: set[str] = set()
        self.namespace: str = ""
        self.summary: str = ""
        self.importance: float = 0.0
        self.boundary_score: float = 0.0  # How distinct this episode is

    def add_record(self, record: EmberRecord):
        self.record_ids.append(record.id)
        created_at = _tz_safe(record.created_at)
        if self.start_time is None or created_at < self.start_time:
            self.start_time = created_at
        if self.end_time is None or created_at > self.end_time:
            self.end_time = created_at
        self.tags.update(record.tags)
        self.namespace = record.namespace

    @property
    def duration(self) -> timedelta | None:
        if self.start_time and self.end_time:
            return self.end_time - self.start_time
        return None

    @property
    def size(self) -> int:
        return len(self.record_ids)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "record_ids": self.record_ids,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "tags": list(self.tags),
            "namespace": self.namespace,
            "summary": self.summary,
            "importance": self.importance,
            "boundary_score": self.boundary_score,
            "size": self.size,
        }


class EpisodicSegmenter:
    """
    Segments a stream of records into coherent episodes.
    
    Uses three signals for boundary detection:
    1. Temporal gaps: large time gaps between records
    2. Topic shift: change in tags/namespace
    3. Surprise: unexpected content relative to running context
    
    Inspired by:
    - EM-LLM (Fountas et al. 2024): Bayesian surprise + graph refinement
    - Human episodic memory: event segmentation theory
    """

    def __init__(self,
                 temporal_gap_minutes: float = 30.0,
                 tag_overlap_threshold: float = 0.3,
                 surprise_threshold: float = 2.0,
                 min_episode_size: int = 1,
                 max_episode_size: int = 100):
        self.temporal_gap = timedelta(minutes=temporal_gap_minutes)
        self.tag_overlap_threshold = tag_overlap_threshold
        self.surprise_threshold = surprise_threshold
        self.min_episode_size = min_episode_size
        self.max_episode_size = max_episode_size

        # Running statistics for surprise detection
        self._tag_frequencies: dict[str, int] = defaultdict(int)
        self._total_records_seen: int = 0

        # Stored episodes
        self._episodes: dict[str, Episode] = {}
        self._record_to_episode: dict[str, str] = {}  # rid → episode_id

    def segment(self, records: list[EmberRecord]) -> list[Episode]:
        """
        Segment a list of records into episodes.
        Records should be in chronological order.
        """
        if not records:
            return []

        sorted_records = sorted(records, key=lambda r: _tz_safe(r.created_at))
        episodes = []
        current_episode = Episode()
        current_episode.add_record(sorted_records[0])

        for i in range(1, len(sorted_records)):
            prev = sorted_records[i - 1]
            curr = sorted_records[i]

            if self._is_boundary(prev, curr, current_episode):
                # Close current episode
                current_episode.importance = self._compute_importance(current_episode)
                episodes.append(current_episode)

                # Start new episode
                current_episode = Episode()

            current_episode.add_record(curr)

            # Max size check
            if current_episode.size >= self.max_episode_size:
                current_episode.importance = self._compute_importance(current_episode)
                episodes.append(current_episode)
                current_episode = Episode()

        # Close last episode
        if current_episode.size > 0:
            current_episode.importance = self._compute_importance(current_episode)
            episodes.append(current_episode)

        # Store episodes
        for ep in episodes:
            self._episodes[ep.id] = ep
            for rid in ep.record_ids:
                self._record_to_episode[rid] = ep.id

        return episodes

    def _is_boundary(self, prev: EmberRecord, curr: EmberRecord,
                     current_episode: Episode) -> bool:
        """
        Detect if there's an episode boundary between prev and curr.
        Uses multiple signals:
        """
        scores = []

        # 1. Temporal gap
        time_gap = _tz_safe(curr.created_at) - _tz_safe(prev.created_at)
        if time_gap > self.temporal_gap:
            scores.append(1.0)
        else:
            gap_ratio = time_gap.total_seconds() / self.temporal_gap.total_seconds()
            scores.append(gap_ratio)

        # 2. Tag shift (topic change)
        if prev.tags and curr.tags:
            prev_set = set(prev.tags)
            curr_set = set(curr.tags)
            overlap = len(prev_set & curr_set)
            union = len(prev_set | curr_set)
            jaccard = overlap / union if union > 0 else 0
            if jaccard < self.tag_overlap_threshold:
                scores.append(1.0)
            else:
                scores.append(1.0 - jaccard)
        elif prev.tags or curr.tags:
            scores.append(0.8)  # One has tags, other doesn't

        # 3. Namespace change
        if prev.namespace != curr.namespace:
            scores.append(1.0)

        # 4. Surprise (based on tag rarity)
        surprise = self._compute_surprise(curr)
        if surprise > self.surprise_threshold:
            scores.append(1.0)
        else:
            scores.append(surprise / self.surprise_threshold)

        # Boundary decision: if any signal is very strong (>0.9), it's a boundary
        # Otherwise, use weighted average
        if not scores:
            return False
        if max(scores) > 0.9:
            return True
        avg_score = sum(scores) / len(scores)
        return avg_score > 0.5

    def _compute_surprise(self, record: EmberRecord) -> float:
        """
        Compute surprise of a record based on how unexpected its tags are.
        Higher surprise = more likely to be an episode boundary.
        Uses information-theoretic surprise: -log(P(tags)).
        """
        self._total_records_seen += 1

        if not record.tags:
            return 0.0

        surprise = 0.0
        for tag in record.tags:
            freq = self._tag_frequencies.get(tag, 0)
            prob = (freq + 1) / (self._total_records_seen + 1)  # Laplace smoothing
            surprise += -math.log(prob)
            self._tag_frequencies[tag] = freq + 1

        return surprise / len(record.tags)

    def _compute_importance(self, episode: Episode) -> float:
        """
        Compute the importance of an episode.
        Factors: size, tag diversity, boundary distinctness.
        """
        size_score = min(episode.size / 10.0, 1.0)
        tag_diversity = min(len(episode.tags) / 5.0, 1.0)
        return (size_score + tag_diversity) / 2.0

    # ── Retrieval ─────────────────────────────────────────────────────────────

    def get_episode(self, episode_id: str) -> Episode | None:
        return self._episodes.get(episode_id)

    def get_episode_for_record(self, record_id: str) -> Episode | None:
        ep_id = self._record_to_episode.get(record_id)
        return self._episodes.get(ep_id) if ep_id else None

    def get_recent_episodes(self, limit: int = 10) -> list[Episode]:
        """Get the N most recent episodes."""
        sorted_eps = sorted(
            self._episodes.values(),
            key=lambda e: e.end_time or datetime.min,
            reverse=True
        )
        return sorted_eps[:limit]

    def get_important_episodes(self, limit: int = 10,
                                min_importance: float = 0.0) -> list[Episode]:
        """Get episodes ranked by importance."""
        filtered = [e for e in self._episodes.values()
                    if e.importance >= min_importance]
        filtered.sort(key=lambda e: -e.importance)
        return filtered[:limit]

    def get_contiguous_episodes(self, episode_id: str,
                                 window: int = 2) -> list[Episode]:
        """
        Get temporally contiguous episodes around a target episode.
        Mimics human contiguity-based retrieval.
        """
        target = self._episodes.get(episode_id)
        if not target:
            return []

        sorted_eps = sorted(
            self._episodes.values(),
            key=lambda e: e.start_time or datetime.min
        )

        target_idx = None
        for i, ep in enumerate(sorted_eps):
            if ep.id == episode_id:
                target_idx = i
                break

        if target_idx is None:
            return []

        start = max(0, target_idx - window)
        end = min(len(sorted_eps), target_idx + window + 1)
        return sorted_eps[start:end]

    # ── Stats ─────────────────────────────────────────────────────────────────

    def episode_count(self) -> int:
        return len(self._episodes)

    def stats(self) -> dict:
        episodes = list(self._episodes.values())
        if not episodes:
            return {"episode_count": 0}

        sizes = [e.size for e in episodes]
        importances = [e.importance for e in episodes]

        return {
            "episode_count": len(episodes),
            "avg_episode_size": sum(sizes) / len(sizes),
            "max_episode_size": max(sizes),
            "min_episode_size": min(sizes),
            "avg_importance": sum(importances) / len(importances),
            "total_records_segmented": len(self._record_to_episode),
        }
