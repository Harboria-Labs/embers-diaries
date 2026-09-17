"""Explicit-channel contract and Candidate 04 regression checks."""
import math

import pytest

from embers.core.feedback import Feedback, FeedbackOutcome
from embers.core.record import EmberRecord
from embers.db import EmberDB
from embers.db_feedback import bind
from embers.cognitive.feedback_dynamics import (
    activation_step, bounded_update, pair_weight, preview_memory_bias,
)

bind(EmberDB)


def body(**changes):
    result = dict(
        schema_version=2, channel="relevance", outcome="useful",
        outcome_id="one-result", context_id="debugging",
        context={"task": "debug", "goal": "finish", "constraints": {},
                 "environment": {"build": "fixture"}},
        signal=1.0,
    )
    result.update(changes)
    return result


def report(**changes):
    return Feedback.from_submission("memory", "authenticated-agent", body(**changes))


def test_legacy_round_trip_does_not_add_schema_fields():
    old = Feedback(memory_id="m", agent_id="a", outcome=FeedbackOutcome.USEFUL)
    data = old.to_dict()
    assert "schema_version" not in data
    assert Feedback.from_dict(data).to_dict() == data
    with pytest.raises(ValueError, match="legacy"):
        preview_memory_bias(0, old, .25)


def test_v2_round_trip_and_authenticated_identity():
    fb = report(agent_id="spoofed", token="not-stored")
    data = fb.to_dict()
    assert data["agent_id"] == "authenticated-agent"
    assert "token" not in data
    assert Feedback.from_dict(data).to_dict() == data


@pytest.mark.parametrize("changes", [
    {"schema_version": True}, {"schema_version": 3}, {"schema_version": 1},
    {"context_id": ""}, {"outcome_id": None}, {"context": []},
    {"context": {"bad": float("nan")}}, {"signal": True},
    {"signal": float("nan")}, {"signal": float("inf")}, {"signal": 1.1},
    {"signal": -1}, {"accuracy": .9}, {"usefulness": .9},
    {"outcome": "confirmed"}, {"supporting_refs": "not-a-list"},
    {"expected_revision": 1}, {"channel": "both"},
])
def test_invalid_or_unsupported_reports_fail(changes):
    with pytest.raises(ValueError):
        report(**changes)


def test_correctness_is_not_relevance_or_truth_promotion():
    fb = report(channel="correctness", outcome="contradicted",
                signal=None, supporting_refs=["observation-1"])
    before = fb.to_dict()
    assert preview_memory_bias(.5, fb, .25) == .5
    assert fb.to_dict() == before
    assert "verified" not in before
    with pytest.raises(ValueError):
        report(channel="correctness", outcome="correct", signal=None,
               supporting_refs=[])
    with pytest.raises(ValueError):
        report(channel="correctness", outcome="correct", signal=1,
               supporting_refs=["e"])


def test_relevance_preview_has_no_side_effects():
    fb = report()
    before = fb.to_dict()
    assert preview_memory_bias(0, fb, .25) == .25
    assert preview_memory_bias(0, fb, .25) == .25  # pure preview, no hidden state
    assert fb.to_dict() == before


def test_saturation_and_reversal():
    for count in (4, 100, 10000):
        value = 0
        for _ in range(count):
            value = bounded_update(value, 1, .25)
        assert value == 1
        for _ in range(8):
            value = bounded_update(value, -1, .25)
        assert value == -1
    assert pair_weight(-.5) == 0
    assert pair_weight(.5) == .5


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), 2])
def test_invalid_bias(value):
    with pytest.raises(ValueError):
        bounded_update(value, 1, .25)


PARAMS = dict(epsilon=.02, mu=.5, delta=.05, rho=1)


def test_activation_conservation_floors_and_recovery():
    for active in (.02, .5, .98):
        for direct in (0, .8, 1):
            for bias in (-1, 0, 1):
                for elapsed in (0, 1e-12, 3, 1e6):
                    a, latent = activation_step(
                        active, direct=direct, cue=direct, bias=bias,
                        elapsed=elapsed, **PARAMS,
                    )
                    assert a + latent == pytest.approx(1, abs=1e-12)
                    assert .02 - 1e-12 <= min(a, latent)
                    assert max(a, latent) <= .98 + 1e-12
    a, _ = activation_step(
        .02, direct=.8, cue=.8, bias=-1, elapsed=3, **PARAMS,
    )
    expected = .532 + (.02 - .532) * math.exp(-.75 * 3)
    assert a == pytest.approx(expected, abs=1e-12)
    assert a > .4


def test_fixed_input_step_composition():
    args = dict(direct=.8, cue=.9, bias=.3, **PARAMS)
    first, _ = activation_step(.1, elapsed=1, **args)
    second, _ = activation_step(first, elapsed=2, **args)
    whole, _ = activation_step(.1, elapsed=3, **args)
    assert second == pytest.approx(whole, abs=1e-12)


@pytest.mark.parametrize("changes", [
    {"epsilon": 0}, {"mu": 1}, {"elapsed": -1}, {"rho": 0},
    {"delta": float("nan")}, {"cue": .1}, {"elapsed": True},
])
def test_invalid_activation_parameters(changes):
    args = dict(direct=.8, cue=.8, bias=0, elapsed=1, **PARAMS)
    args.update(changes)
    with pytest.raises(ValueError):
        activation_step(.5, **args)


def test_sdk_persists_channel_without_mutating_memory(tmp_path):
    db = EmberDB.connect(str(tmp_path / "store"))
    mid = db.write(EmberRecord(namespace="memories", data={"content": "a fact"}))
    before = db.get(mid).content_hash
    fb = Feedback.from_submission(mid, "agent", body())
    fid = db.give_feedback(mid, fb)
    assert db.get_feedback(fid).to_dict() == fb.to_dict()
    assert db.get(mid).content_hash == before
    assert db.get(mid).confidence == 1.0
    assert db.get(fid).namespace == "memories"
    fb.memory_id = "wrong-target"
    with pytest.raises(ValueError, match="match"):
        db.give_feedback(mid, fb)
