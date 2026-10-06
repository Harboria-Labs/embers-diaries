"""
Ember's Diaries — Context Builder
Transforms Ember records into LLM-consumable context.

Three injection modes:
1. Text injection: serialize records as structured text in the prompt
2. Message injection: format records as chat messages (system/user)
3. Structured injection: JSON format for function-calling models

Inspired by Memory³'s explicit memory injection and MemOS's MemCube
serialization, but built on Ember's immutable record model.
"""

from datetime import datetime, timezone
from typing import Any

from ..core.record import EmberRecord
from ..core.annotation import Annotation
from ..cognitive.decay import DecayEngine


class ContextBuilder:
    """
    Builds LLM context from Ember records.
    
    Handles:
    - Record selection (by confidence, recency, relevance)
    - Serialization format (text, messages, JSON)
    - Token budget management
    - Provenance tracking (which records were injected)
    """

    def __init__(self, decay_engine: DecayEngine | None = None,
                 max_tokens: int = 4096,
                 chars_per_token: float = 4.0):
        self._decay = decay_engine or DecayEngine()
        self.max_tokens = max_tokens
        self._chars_per_token = chars_per_token
        self._last_injected: list[str] = []  # Track what we last injected

    def _estimate_tokens(self, text: str) -> int:
        return int(len(text) / self._chars_per_token)

    # ── Text Injection ────────────────────────────────────────────────────────

    def build_text_context(self, records: list[EmberRecord],
                           include_annotations: bool = True,
                           include_confidence: bool = True,
                           include_history_hint: bool = True,
                           header: str = "## Memory Context") -> str:
        """
        Build a text block suitable for system prompt injection.
        Records are formatted as structured text with provenance.
        """
        now = datetime.now(timezone.utc)
        parts = [header, ""]

        used_tokens = self._estimate_tokens(header)
        injected_ids = []

        # Sort by effective confidence (highest first)
        scored_records = []
        for r in records:
            eff = self._decay.effective_confidence(r, now)
            scored_records.append((r, eff))
        scored_records.sort(key=lambda x: -x[1])

        for record, eff_conf in scored_records:
            block = self._format_record_text(
                record, eff_conf, include_annotations,
                include_confidence, include_history_hint)

            block_tokens = self._estimate_tokens(block)
            if used_tokens + block_tokens > self.max_tokens:
                break

            parts.append(block)
            used_tokens += block_tokens
            injected_ids.append(record.id)

        self._last_injected = injected_ids

        if len(injected_ids) == 0:
            return ""

        parts.append(f"\n---\n[{len(injected_ids)} memories loaded]")
        return "\n".join(parts)

    def _format_record_text(self, record: EmberRecord,
                             effective_confidence: float,
                             include_annotations: bool,
                             include_confidence: bool,
                             include_history_hint: bool) -> str:
        """Format a single record as text."""
        lines = []
        lines.append(f"### Memory [{record.id[:8]}]")

        # Data
        if isinstance(record.data, dict):
            for k, v in record.data.items():
                lines.append(f"  {k}: {v}")
        elif record.data is not None:
            lines.append(f"  {record.data}")

        # Metadata
        meta_parts = []
        if record.tags:
            meta_parts.append(f"tags: {', '.join(record.tags)}")
        if include_confidence:
            meta_parts.append(f"confidence: {effective_confidence:.2f}")
        meta_parts.append(f"written: {record.created_at.strftime('%Y-%m-%d %H:%M')}")
        if record.written_by != "system":
            meta_parts.append(f"by: {record.written_by}")
        if meta_parts:
            lines.append(f"  [{' | '.join(meta_parts)}]")

        # History hint
        if include_history_hint and record.supersedes:
            lines.append(f"  [updated from: {record.supersedes[:8]}]")

        # Annotations
        if include_annotations and record.annotations:
            for ann in record.annotations[-3:]:  # Last 3 annotations
                lines.append(f"  📝 {ann.content} ({ann.written_by})")

        lines.append("")
        return "\n".join(lines)

    # ── Message Injection ─────────────────────────────────────────────────────

    def build_message_context(self, records: list[EmberRecord],
                               role: str = "system") -> list[dict]:
        """
        Build chat messages from records.
        Each record becomes a message suitable for chat completion APIs.
        """
        messages = []
        now = datetime.now(timezone.utc)

        scored = [(r, self._decay.effective_confidence(r, now)) for r in records]
        scored.sort(key=lambda x: -x[1])

        total_chars = 0
        for record, eff_conf in scored:
            content = self._format_record_message(record, eff_conf)
            total_chars += len(content)
            if total_chars / self._chars_per_token > self.max_tokens:
                break

            messages.append({
                "role": role,
                "content": content,
                "metadata": {
                    "ember_record_id": record.id,
                    "confidence": eff_conf,
                    "namespace": record.namespace,
                }
            })
            self._last_injected.append(record.id)

        return messages

    @staticmethod
    def _explicit_status(record):
        # Ordinary legacy message recall must not present stored verification
        # labels or verification annotations as current truth. Keep a fixed
        # pointer in capacity-counted content; canonical state lives in ELLA.
        return "see_epistemic_state"

    def _format_record_message(self, record: EmberRecord,
                                effective_confidence: float) -> str:
        """Format a record as a chat message content string."""
        if isinstance(record.data, dict):
            content = record.data.get("content",
                       record.data.get("text",
                       record.data.get("summary", str(record.data))))
        elif isinstance(record.data, str):
            content = record.data
        else:
            content = str(record.data) if record.data else ""

        return (
            f"[Memory {record.id[:8]} | "
            f"confidence: {effective_confidence:.2f} | "
            f"{self._explicit_status(record)}]\n"
            f"{content}"
        )

    # ── Structured (JSON) Injection ───────────────────────────────────────────

    def build_structured_context(self, records: list[EmberRecord]) -> list[dict]:
        """
        Build structured JSON context for function-calling models.
        Each record is a dict with standardized fields.
        """
        now = datetime.now(timezone.utc)
        result = []

        for record in records:
            eff_conf = self._decay.effective_confidence(record, now)
            result.append({
                "id": record.id,
                "namespace": record.namespace,
                "data": record.data,
                "confidence": round(eff_conf, 3),
                "tags": record.tags,
                "created_at": record.created_at.isoformat(),
                "written_by": record.written_by,
                "is_current": record.is_current,
                "annotations_count": len(record.annotations),
                "supersedes": record.supersedes,
            })
            self._last_injected.append(record.id)

        return result

    # ── Utilities ─────────────────────────────────────────────────────────────

    def get_last_injected(self) -> list[str]:
        """Record IDs that were included in the last context build."""
        return list(self._last_injected)

    def clear_history(self):
        self._last_injected.clear()
