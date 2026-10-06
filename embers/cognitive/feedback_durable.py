"""Opt-in SDK persistence for canonical relevance decisions.

Uses the existing Ember record transaction and native store lock. This is NOT
automatic recall or a total-store quota implementation. Configure this service
from trusted application code, never from an unauthenticated request body.
"""
from __future__ import annotations

from dataclasses import asdict
import json
import uuid

from .feedback_replay import (
    Credit, Dependency, RelevanceDecision, RelevanceJournal, Revision,
    REPLAY_VERSION,
)
from ..core.feedback import Feedback
from ..core.record import EmberRecord
from ..core.types import RecordType
from ..storage.store import STORE_LOCK_BACKEND

_KIND = "ember.canonical-relevance.v1"


def _uuid(text):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, text))


def _revision(data):
    data = dict(data)
    decision = dict(data.pop("decision"))
    decision["credits"] = tuple(Credit(**c) for c in decision["credits"])
    decision["depends_on"] = tuple(Dependency(**d) for d in decision["depends_on"])
    decision["report_ids"] = tuple(decision["report_ids"])
    return Revision(decision=RelevanceDecision(**decision), **data)


class DurableRelevanceJournal:
    """One immutable policy and one event record per effective revision.

    Rebuild on each operation avoids stale in-memory projections across handles.
    This is deliberately a correctness-first implementation, not a scalable
    checkpoint implementation. Namespace access is a trusted SDK caller duty.
    """

    def __init__(self, db, *, namespace, context_id, context,
                 authorized_resolvers, memory_rate, pair_rate):
        if STORE_LOCK_BACKEND != "rust-pyo3":
            raise RuntimeError("durable resolution requires native inter-process locking")
        if not isinstance(context, dict):
            raise ValueError("context must be an object")
        # Validate before making any store changes.
        RelevanceJournal(namespace=namespace, context_id=context_id,
                         authorized_resolvers=authorized_resolvers,
                         memory_rate=memory_rate, pair_rate=pair_rate)
        context_json = json.dumps(context, sort_keys=True, allow_nan=False,
                                  separators=(",", ":"))
        self._db = db
        self._store = db._store
        self._namespace = namespace
        self._context_id = context_id
        self._root_id = _uuid(json.dumps([_KIND, namespace, context_id]))
        self._policy = dict(
            model_version=REPLAY_VERSION, namespace=namespace,
            context_id=context_id, context_json=context_json,
            authorized_resolvers=sorted(authorized_resolvers),
            memory_rate=memory_rate, pair_rate=pair_rate,
        )
        with db._writer.lock:
            existing = self._store.read(self._root_id)
            if existing is None:
                if self._store.exists(self._root_id):
                    raise ValueError("unreadable journal policy")
                self._write(self._root_id, dict(
                    kind=_KIND, journal_id=self._root_id, entry="policy",
                    policy=self._policy,
                ), "system")
            else:
                if (existing.record_type != RecordType.RAW or
                        existing.namespace != namespace or
                        existing.data != dict(
                            kind=_KIND, journal_id=self._root_id, entry="policy",
                            policy=self._policy)):
                    raise ValueError("journal policy differs; explicit migration required")

    def _write(self, record_id, payload, actor):
        record = EmberRecord(
            id=record_id, namespace=self._namespace, record_type=RecordType.RAW,
            data=payload, written_by=actor, agent_id=actor,
            retrieval_candidate=False, training_candidate=False,
            tags=[_KIND],
        )
        return self._db._writer.write(record)

    def _configuration(self):
        return dict(
            namespace=self._namespace, context_id=self._context_id,
            authorized_resolvers=frozenset(self._policy["authorized_resolvers"]),
            memory_rate=self._policy["memory_rate"],
            pair_rate=self._policy["pair_rate"],
        )

    def _load(self):
        # Read the physical source rather than potentially stale/callback-failed
        # derived indexes. Fail closed instead of silently dropping unreadable data.
        events = []
        for record_id in self._store.all_ids():
            record = self._store.read(record_id)
            if record is None:
                raise ValueError(f"cannot rebuild with unreadable record {record_id}")
            data = record.data
            if not isinstance(data, dict) or data.get("journal_id") != self._root_id:
                continue
            if record.namespace != self._namespace or record.record_type != RecordType.RAW:
                raise ValueError("invalid journal record scope/type")
            if not record.verify_integrity():
                raise ValueError("journal record lacks integrity seal")
            if data.get("entry") == "policy":
                if record.id != self._root_id or data.get("policy") != self._policy:
                    raise ValueError("journal policy mismatch")
                continue
            if data.get("kind") != _KIND or data.get("entry") != "revision":
                raise ValueError("unsupported journal entry")
            sequence = data.get("sequence")
            if type(sequence) is not int or sequence < 1:
                raise ValueError("invalid journal sequence")
            if record.id != _uuid(self._root_id + ":" + str(sequence)):
                raise ValueError("journal record identity mismatch")
            events.append((sequence, record))
        events.sort(key=lambda pair: pair[0])
        history = []
        previous = self._store.read(self._root_id)
        if previous is None or not previous.verify_integrity():
            raise ValueError("journal policy is missing or unsealed")
        for expected, (sequence, record) in enumerate(events, 1):
            if sequence != expected or record.data["previous_hash"] != previous.content_hash:
                raise ValueError("journal sequence/hash chain is broken")
            revision = _revision(record.data["revision"])
            if record.written_by != revision.actor or record.agent_id != revision.actor:
                raise ValueError("journal actor mismatch")
            history.append(revision)
            previous = record
        journal = RelevanceJournal.from_history(tuple(history), **self._configuration())
        return journal, previous.content_hash

    def _validate_sources(self, decision):
        report_memories = set()
        for report_id in decision.report_ids:
            record = self._store.read(report_id)
            if (record is None or record.record_type != RecordType.FEEDBACK or
                    record.namespace != self._namespace):
                raise ValueError("source report missing or outside namespace")
            fb = Feedback.from_dict(record.data)
            fb.validate()
            if (fb.schema_version != 2 or fb.channel != "relevance" or
                    fb.context_id != self._context_id or
                    fb.outcome_id != decision.outcome_id):
                raise ValueError("source report channel/context/outcome mismatch")
            if json.dumps(fb.context, sort_keys=True, allow_nan=False,
                          separators=(",", ":")) != self._policy["context_json"]:
                raise ValueError("source context descriptor differs")
            report_memories.add(fb.memory_id)
        for credit in decision.credits:
            targets = [credit.memory_version]
            if credit.to_version is not None:
                targets.append(credit.to_version)
            for target_id in targets:
                target = self._store.read(target_id)
                if (target is None or target.namespace != self._namespace or
                        target.record_type not in self._db._DURABLE_MEMORY_TYPES):
                    raise ValueError("credit target missing or outside namespace")
            if credit.memory_version not in report_memories:
                raise ValueError("credit source must be addressed by a cited report")

    def resolve(self, decision, *, actor, request_id, expected_revision):
        self._db.require_namespace_access(self._namespace, actor, "write")
        with self._db._writer.lock:
            journal, previous_hash = self._load()
            before = len(journal.history())
            revision = journal.resolve(
                decision, actor=actor, request_id=request_id,
                expected_revision=expected_revision,
            )
            if len(journal.history()) == before:
                return revision
            self._validate_sources(decision)
            sequence = before + 1
            # Only the immutable event is authoritative. No mutable projection is
            # acknowledged, so callback lag cannot falsely claim learned state.
            self._write(
                _uuid(self._root_id + ":" + str(sequence)),
                dict(kind=_KIND, journal_id=self._root_id, entry="revision",
                     sequence=sequence, previous_hash=previous_hash,
                     revision=asdict(revision)),
                actor,
            )
            return revision

    def project(self):
        with self._db._writer.lock:
            journal, _ = self._load()
            return journal.project()

    def history(self):
        with self._db._writer.lock:
            journal, _ = self._load()
            return journal.history()
