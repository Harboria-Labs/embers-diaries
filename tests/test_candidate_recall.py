import json
from dataclasses import replace
import pytest
from test_feedback_durable import setup, resolve, CONFIG
from embers.db import EmberDB
from embers.core.record import EmberRecord
from embers.cognitive.feedback_replay import Credit
from embers.integration.candidate_recall import CandidateRecall, CandidatePolicy


def service(journal, **kw):
    # Byte counter is a deterministic fixture, not a model tokenizer.
    return CandidateRecall(journal, token_counter=lambda s: len(s.encode()),
        tokenizer_id='utf8-bytes-test', policy=CandidatePolicy(token_budget=10000,
        memory_token_cap=3000, neighborhood_token_cap=6000, **kw))


@pytest.mark.parametrize('format', ['structured', 'text', 'messages'])
def test_budget_retry_restart_and_no_learning(tmp_path, format):
    db, journal, decision, path = setup(tmp_path)
    mid = decision.credits[0].memory_version
    before = db.get(mid).to_dict()
    recall = service(journal)
    args = dict(actor='resolver', query_id='q', direct_scores={mid: 1}, elapsed=2, format=format)
    result = recall.recall(**args)
    assert result['selected_ids'] == [mid]
    assert result['token_count'] == len(result['context'].encode()) <= 10000
    assert journal.project().generation == 0
    assert db.get(mid).to_dict() == before
    assert recall.recall(**args) == result
    reopened = service(EmberDB.connect(path).relevance_journal(**CONFIG))
    assert reopened.recall(**args) == result
    with pytest.raises(ValueError, match='different input'):
        reopened.recall(**dict(args, elapsed=3))


def test_directional_pairs_and_truth_label(tmp_path):
    db, journal, decision, _ = setup(tmp_path)
    mid = decision.credits[0].memory_version
    neighbor = db.write(EmberRecord(namespace='memories', data={'content': 'old wrong idea', 'verify_status': 'incorrect'}))
    resolve(journal, replace(decision, credits=(Credit(mid, 1, neighbor, 'warns_about'),)))
    result = service(journal).recall(actor='resolver', query_id='q', direct_scores={mid: 1}, elapsed=2)
    assert result['edges_examined'] == 1
    # Latent inspection permits a useful false neighbor without promoting truth.
    rows = json.loads(result['context'])
    if neighbor not in result['selected_ids']:
        result = service(journal).recall(actor='resolver', query_id='q2', direct_scores={mid: 1}, elapsed=2)
        rows = json.loads(result['context'])
    assert next(r for r in rows if r['id'] == neighbor)['truth_status'] == 'incorrect'
    reverse = service(journal).recall(actor='resolver', query_id='r', direct_scores={neighbor: 1}, elapsed=2)
    assert reverse['edges_examined'] == 0
    assert mid not in reverse['selected_ids']


def test_latent_inspection_and_floor_validation(tmp_path):
    _, journal, decision, _ = setup(tmp_path)
    mid = decision.credits[0].memory_version
    result = service(journal).recall(actor='resolver', query_id='q', direct_scores={mid: .01}, elapsed=0)
    assert result['selected_ids'] == [mid]
    assert json.loads(result['context'])[0]['activation'] == .02
    with pytest.raises(ValueError, match='threshold'):
        CandidatePolicy(threshold=.9)


def test_oversize_does_not_hide_affordable_alternative(tmp_path):
    db, journal, decision, _ = setup(tmp_path)
    mid = decision.credits[0].memory_version
    large = db.write(EmberRecord(namespace='memories', data={'content': 'x' * 20000}))
    result = service(journal).recall(actor='resolver', query_id='q', direct_scores={mid: 1, large: 1}, elapsed=10)
    assert result['selected_ids'] == [mid]


def test_wrong_namespace_and_nonfinite_rejected(tmp_path):
    db, journal, _, _ = setup(tmp_path)
    outside = db.write(EmberRecord(namespace='other', data={'content': 'outside'}))
    recall = service(journal)
    for scores in ({outside: 1}, {outside: float('nan')}):
        with pytest.raises(ValueError):
            recall.recall(actor='resolver', query_id='q', direct_scores=scores, elapsed=1)
