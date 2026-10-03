"""Canonical relevance resolution: invariants, not live-store validation."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from embers.cognitive.feedback_replay import (
    Credit, Dependency, RelevanceDecision, RelevanceJournal, RevisionConflict,
)

CONFIG = dict(namespace="memories", context_id="debug",
              authorized_resolvers=frozenset(("resolver", "second")),
              memory_rate=.25, pair_rate=.25)


def journal():
    return RelevanceJournal(**CONFIG)


def decision(outcome="result", value=1, **changes):
    values = dict(
        outcome_id=outcome, status="accepted",
        credits=(Credit("memory-v1", value),),
        report_ids=("report-" + outcome,), reason="observed contribution",
    )
    values.update(changes)
    return RelevanceDecision(**values)


def submit(log, item, request="req", revision=0, actor="resolver"):
    return log.resolve(item, actor=actor, request_id=request,
                       expected_revision=revision)


def test_same_request_and_underlying_outcome_never_reinforce_twice():
    log = journal()
    item = decision()
    first = submit(log, item)
    for _ in range(1000):
        assert submit(log, item) == first
    # A different reporter/request cannot create a second copy of this outcome.
    assert submit(log, item, request="different", actor="second") == first
    assert len(log.history()) == 1
    assert log.project().memory_bias == {"memory-v1": .25}


def test_changed_request_content_and_stale_revision_fail_atomically():
    log = journal()
    submit(log, decision())
    before = log.history()
    with pytest.raises(RevisionConflict):
        submit(log, decision(value=-1))
    with pytest.raises(RevisionConflict):
        submit(log, decision(value=-1), request="new")
    assert log.history() == before


def test_repeated_current_decision_does_not_invalidate_dependencies():
    log = journal()
    first = submit(log, decision("a"), request="a")
    submit(log, decision("b", depends_on=(Dependency("a", 1),)), request="b")
    assert submit(log, decision("a"), request="again", revision=1) == first
    assert log.project().effective_revisions == {"a": 1, "b": 1}
    assert len(log.history()) == 2


def test_correcting_saturated_history_replays_original_order():
    log = journal()
    for i in range(5):
        submit(log, decision(str(i)), request=str(i))
    assert log.project().memory_bias["memory-v1"] == 1
    submit(log, decision("0", value=-1), request="correction", revision=1)
    # Correct sequence: -.25 + .25 + .25 + .25 + .25 = .75.
    # Subtracting historical credit from today's clipped state is incorrect.
    assert log.project().memory_bias["memory-v1"] == .75
    assert len(log.history()) == 6
    assert log.history()[0].decision.credits[0].value == 1
    assert log.history()[-1].order == 0


def test_dependency_invalidation_is_transitive_and_preserves_independent_credit():
    log = journal()
    submit(log, decision("a"), request="a")
    submit(log, decision("b", depends_on=(Dependency("a", 1),)), request="b")
    submit(log, decision("c", depends_on=(Dependency("b", 1),)), request="c")
    submit(log, decision("independent"), request="independent")
    submit(log, decision("a", status="retracted", credits=()),
           request="retract", revision=1)
    projection = log.project()
    assert projection.memory_bias == {"memory-v1": .25}
    assert projection.effective_revisions == {"independent": 1}
    assert projection.inactive["b"] == "dependency_requires_revalidation"
    assert projection.inactive["c"] == "dependency_requires_revalidation"


def test_explicit_revalidation_restores_only_current_dependencies():
    log = journal()
    submit(log, decision("a"), request="a")
    submit(log, decision("b", depends_on=(Dependency("a", 1),)), request="b")
    submit(log, decision("a", value=-1), request="fix-a", revision=1)
    assert "b" not in log.project().effective_revisions
    submit(log, decision("b", depends_on=(Dependency("a", 2),)),
           request="revalidate-b", revision=1)
    assert log.project().effective_revisions == {"a": 2, "b": 2}
    assert log.project().memory_bias["memory-v1"] == 0


def test_pending_group_and_disagreement_do_not_receive_automatic_credit():
    log = journal()
    submit(log, decision("group", status="pending", credits=(),
                         report_ids=("agent-a", "agent-b"),
                         reason="individual roles unresolved"))
    assert log.project().memory_bias == {}
    assert log.project().inactive == {"group": "pending"}
    with pytest.raises(ValueError):
        decision("group", status="pending")  # no attributed credit while pending


def test_typed_pair_is_directional_and_uses_shared_credit_budget():
    log = journal()
    submit(log, decision(credits=(
        Credit("a-v1", .5),
        Credit("a-v1", .5, "b-v1", "warns_about"),
    )))
    projection = log.project()
    assert projection.memory_bias == {"a-v1": .125}
    assert projection.pair_state == {("a-v1", "b-v1", "warns_about"): .125}
    assert ("b-v1", "a-v1", "warns_about") not in projection.pair_state
    with pytest.raises(ValueError):
        decision(credits=(Credit("a", .75), Credit("b", .75)))


def test_dependency_cycles_forward_references_and_stale_versions_rejected():
    log = journal()
    with pytest.raises(ValueError):
        submit(log, decision("a", depends_on=(Dependency("a", 1),)))
    submit(log, decision("a"), request="a")
    submit(log, decision("b"), request="b")
    with pytest.raises(ValueError):
        submit(log, decision("a", depends_on=(Dependency("b", 1),)),
               request="cycle", revision=1)
    with pytest.raises(RevisionConflict):
        submit(log, decision("c", depends_on=(Dependency("a", 99),)), request="c")


def test_authority_and_context_separation():
    one = journal()
    with pytest.raises(PermissionError):
        submit(one, decision(), actor="untrusted")
    submit(one, decision())
    other = RelevanceJournal(**dict(CONFIG, context_id="different-task"))
    assert other.project().memory_bias == {}
    assert one.project().context_id == "debug"
    assert one.project().generation == 1


def test_competing_corrections_use_compare_and_set():
    log = journal()
    submit(log, decision())
    def attempt(value):
        try:
            submit(log, decision(value=value), request=str(value), revision=1)
            return "accepted"
        except RevisionConflict:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [-1, .5]))
    assert sorted(results) == ["accepted", "conflict"]
    assert len(log.history()) == 2


def test_history_rebuild_matches_state_after_corrections_and_retry():
    log = journal()
    original = submit(log, decision("a"), request="a")
    submit(log, decision("b", depends_on=(Dependency("a", 1),)), request="b")
    submit(log, decision("a", value=-1), request="fix", revision=1)
    # The old request returns its receipt, never restores the old learning.
    assert submit(log, decision("a"), request="a") == original
    rebuilt = RelevanceJournal.from_history(log.history(), **CONFIG)
    assert rebuilt.history() == log.history()
    assert rebuilt.project() == log.project()
    assert submit(rebuilt, decision("a"), request="a") == original
    with pytest.raises(ValueError):
        RelevanceJournal.from_history(log.history() + (log.history()[-1],), **CONFIG)
    with pytest.raises(ValueError):
        RelevanceJournal.from_history((replace(log.history()[0], order=8),), **CONFIG)


def test_results_are_detached_and_truth_is_absent():
    log = journal()
    submit(log, decision())
    view = log.project()
    view.memory_bias["memory-v1"] = 99
    assert log.project().memory_bias["memory-v1"] == .25
    assert not hasattr(view, "truth")
    with pytest.raises(TypeError):
        RelevanceDecision(outcome_id="truth", status="accepted", channel="correctness")


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), 2, -2])
def test_invalid_credit(value):
    with pytest.raises(ValueError):
        Credit("m", value)


def test_invalid_shapes_fail_before_any_transition():
    with pytest.raises(ValueError):
        decision(credits=[Credit("m", 1)])
    with pytest.raises(ValueError):
        decision(credits=(Credit("m", .5), Credit("m", .5)))
    with pytest.raises(ValueError):
        decision(report_ids=())
    with pytest.raises(ValueError):
        Credit("a", .5, "b", "untyped")
    with pytest.raises(ValueError):
        Dependency("a", True)
