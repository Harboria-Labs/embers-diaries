"""Experimental, in-process canonical relevance journal.

Raw agent reports are NOT decisions. A trusted caller must authenticate the
resolver, resolve disagreements and bind context/target permissions before use.
No truth state, persistent store or live recall policy is accessed here.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from threading import RLock

from .feedback_dynamics import DYNAMICS_VERSION, bounded_update

REPLAY_VERSION = DYNAMICS_VERSION + "/canonical-v1"

RELATIONS = frozenset(("explains", "requires", "warns_about", "alternative"))


def _identifier(name, value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


def _integer(name, value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


@dataclass(frozen=True)
class Credit:
    """One immutable memory version, or a directed typed pair of versions."""
    memory_version: str
    value: float
    to_version: str | None = None
    relation: str | None = None

    def __post_init__(self):
        _identifier("memory_version", self.memory_version)
        if type(self.value) not in (int, float) or not -1 <= self.value <= 1:
            raise ValueError("credit must be finite and within [-1, 1]")
        if self.to_version is None:
            if self.relation is not None:
                raise ValueError("memory credit cannot carry a pair relation")
        else:
            _identifier("to_version", self.to_version)
            if not isinstance(self.relation, str) or self.relation not in RELATIONS:
                raise ValueError("pair requires a supported directional relation")

    @property
    def key(self):
        return (self.memory_version, self.to_version, self.relation)


@dataclass(frozen=True)
class Dependency:
    outcome_id: str
    revision: int

    def __post_init__(self):
        _identifier("outcome_id", self.outcome_id)
        _integer("revision", self.revision, 1)


@dataclass(frozen=True)
class RelevanceDecision:
    outcome_id: str
    status: str
    credits: tuple[Credit, ...] = ()
    depends_on: tuple[Dependency, ...] = ()
    report_ids: tuple[str, ...] = ()
    reason: str = ""

    def __post_init__(self):
        _identifier("outcome_id", self.outcome_id)
        _identifier("reason", self.reason)
        if self.status not in ("accepted", "pending", "retracted"):
            raise ValueError("status must be accepted, pending or retracted")
        for name, kind in (("credits", Credit), ("depends_on", Dependency),
                           ("report_ids", str)):
            values = getattr(self, name)
            if type(values) is not tuple or any(not isinstance(v, kind) for v in values):
                raise ValueError(f"{name} must be an immutable typed tuple")
        for report_id in self.report_ids:
            _identifier("report_id", report_id)
        if len(set(self.report_ids)) != len(self.report_ids):
            raise ValueError("duplicate report references")
        if len({c.key for c in self.credits}) != len(self.credits):
            raise ValueError("duplicate credit target")
        if len({d.outcome_id for d in self.depends_on}) != len(self.depends_on):
            raise ValueError("duplicate dependency")
        if math.fsum(abs(c.value) for c in self.credits) > 1:
            raise ValueError("total absolute outcome credit must not exceed 1")
        if self.status == "accepted":
            if not self.credits or not self.report_ids:
                raise ValueError("accepted decisions need explicit credit and source reports")
        elif self.credits:
            raise ValueError("pending/retracted outcomes cannot assign credit")


@dataclass(frozen=True)
class Revision:
    decision: RelevanceDecision
    revision: int
    order: int
    actor: str
    request_id: str
    expected_revision: int

    def __post_init__(self):
        if not isinstance(self.decision, RelevanceDecision):
            raise ValueError("revision requires a relevance decision")
        _integer("revision", self.revision, 1)
        _integer("order", self.order)
        _integer("expected_revision", self.expected_revision)
        _identifier("actor", self.actor)
        _identifier("request_id", self.request_id)
        if self.revision != self.expected_revision + 1:
            raise ValueError("revision must follow expected_revision")


@dataclass
class Projection:
    """Detached result; mutation of these dictionaries cannot mutate the journal."""
    model_version: str
    generation: int
    namespace: str
    context_id: str
    memory_rate: float
    pair_rate: float
    memory_bias: dict[str, float]
    pair_state: dict[tuple[str, str, str], float]
    effective_revisions: dict[str, int]
    inactive: dict[str, str]


class RevisionConflict(ValueError):
    pass


class RelevanceJournal:
    """Explicit resolution, compare-and-set revisions and full replay.

    Scope is a single namespace/context. Outcome IDs identify underlying results,
    not transport requests. Storage durability and inter-process admission are
    deliberately NOT provided by this experimental journal.
    """

    def __init__(self, *, namespace: str, context_id: str,
                 authorized_resolvers: frozenset[str],
                 memory_rate: float, pair_rate: float):
        _identifier("namespace", namespace)
        _identifier("context_id", context_id)
        if type(authorized_resolvers) is not frozenset or not authorized_resolvers:
            raise ValueError("configure a nonempty immutable resolver allowlist")
        for actor in authorized_resolvers:
            _identifier("resolver", actor)
        # Reuse the kernel's finite bounded-rate validation.
        bounded_update(0, 0, memory_rate)
        bounded_update(0, 0, pair_rate)
        self._namespace = namespace
        self._context_id = context_id
        self._resolvers = authorized_resolvers
        self._memory_rate = memory_rate
        self._pair_rate = pair_rate
        self._history: list[Revision] = []
        self._heads: dict[str, Revision] = {}
        self._requests: dict[tuple[str, str], Revision] = {}
        self._lock = RLock()

    def resolve(self, decision: RelevanceDecision, *, actor: str,
                request_id: str, expected_revision: int) -> Revision:
        if not isinstance(decision, RelevanceDecision):
            raise ValueError("resolve requires an explicit relevance decision")
        _identifier("actor", actor)
        _identifier("request_id", request_id)
        _integer("expected_revision", expected_revision)
        if actor not in self._resolvers:
            raise PermissionError("resolver is not authorized for this journal")
        with self._lock:
            previous_request = self._requests.get((actor, request_id))
            if previous_request is not None:
                if (previous_request.decision != decision or
                        previous_request.expected_revision != expected_revision):
                    raise RevisionConflict("request_id reused with different content")
                return previous_request
            head = self._heads.get(decision.outcome_id)
            current_revision = head.revision if head else 0
            if expected_revision != current_revision:
                # A second report of the same initial resolution earns no credit.
                if expected_revision == 0 and head and head.revision == 1 and head.decision == decision:
                    return head
                raise RevisionConflict("expected_revision does not match current revision")
            if head is not None and head.decision == decision:
                return head  # no generation bump and no dependency invalidation
            order = head.order if head else len(self._heads)
            projection = self._project()
            for dep in decision.depends_on:
                parent = self._heads.get(dep.outcome_id)
                if parent is None or parent.order >= order:
                    raise ValueError("dependencies must refer to earlier outcomes in this context")
                if parent.revision != dep.revision:
                    raise RevisionConflict("dependency revision is stale")
                if decision.status == "accepted" and dep.outcome_id not in projection.effective_revisions:
                    raise ValueError("accepted outcome depends on inactive learning")
            revision = Revision(
                decision, current_revision + 1, order, actor, request_id,
                expected_revision,
            )
            self._history.append(revision)
            self._heads[decision.outcome_id] = revision
            self._requests[(actor, request_id)] = revision
            return revision

    def history(self) -> tuple[Revision, ...]:
        with self._lock:
            return tuple(self._history)

    def project(self) -> Projection:
        with self._lock:
            return self._project()

    def _project(self) -> Projection:
        memory = {}
        pairs = {}
        effective = {}
        inactive = {}
        # Corrections replace the contribution at its ORIGINAL outcome position.
        # Never subtract an old clipped update from the current bias.
        for head in sorted(self._heads.values(), key=lambda item: item.order):
            decision = head.decision
            if decision.status != "accepted":
                inactive[decision.outcome_id] = decision.status
                continue
            if any(effective.get(dep.outcome_id) != dep.revision
                   for dep in decision.depends_on):
                inactive[decision.outcome_id] = "dependency_requires_revalidation"
                continue
            for credit in decision.credits:
                if credit.to_version is None:
                    key = credit.memory_version
                    memory[key] = bounded_update(memory.get(key, 0), credit.value,
                                                 self._memory_rate)
                else:
                    key = credit.key
                    pairs[key] = bounded_update(pairs.get(key, 0), credit.value,
                                               self._pair_rate)
            effective[decision.outcome_id] = head.revision
        return Projection(
            REPLAY_VERSION, len(self._history), self._namespace,
            self._context_id, self._memory_rate, self._pair_rate,
            memory, pairs, effective, inactive,
        )

    @classmethod
    def from_history(cls, history: tuple[Revision, ...], **configuration):
        """Rebuild from trusted typed history; this is not a disk recovery API."""
        journal = cls(**configuration)
        if type(history) is not tuple:
            raise ValueError("history must be an immutable tuple")
        for item in history:
            if not isinstance(item, Revision):
                raise ValueError("history contains an invalid revision")
            rebuilt = journal.resolve(
                item.decision, actor=item.actor, request_id=item.request_id,
                expected_revision=item.expected_revision,
            )
            if rebuilt != item or len(journal._history) == 0 or journal._history[-1] != item:
                raise ValueError("history contains inconsistent revision metadata")
        if len(journal._history) != len(history):
            raise ValueError("history includes duplicate revisions")
        return journal
