"""Contract 01: no semantic inference or learning from primary context."""
import json
import asyncio
from pathlib import Path
import pytest
from embers.db import EmberDB
from embers.integration import MemoryProtocol
from embers.core.feedback import Feedback
from test_feedback_lifecycle import mcp, _body


def test_context_contract_sdk_restart_and_feedback(tmp_path):
    path = str(tmp_path / 'store')
    db = EmberDB.connect(path)
    protocol = MemoryProtocol(db)
    m1 = protocol.remember({'content': 'Ember activation reference', 'subject': 'Ember'}, primary_context='LADC')
    m2 = protocol.remember({'content': 'Ember pairing reference', 'subject': 'Ember'}, primary_context='Pairing Matrix')
    plain = protocol.remember('Ember plain reference')
    before = set(db._store.all_ids())
    response = protocol.recall('Ember', primary_context='LADC', format='structured')
    assert response['query'] == 'Ember'
    assert response['primary_context'] == 'LADC'
    assert {m1, m2, plain} <= {r['id'] for r in response['results']}
    assert set(db._store.all_ids()) == before  # no duplication or implicit context records
    contexts = {r['id']: r['primary_context'] for r in response['results']}
    assert contexts == {m1: 'LADC', m2: 'Pairing Matrix', plain: None}
    fid = db.give_feedback(m2, Feedback.from_submission(m2, 'agent', {
        'outcome': 'useful', 'retrieval_context': response['primary_context']}))
    reopened = EmberDB.connect(path)
    assert reopened.get(m2).data['primary_context'] == 'Pairing Matrix'
    assert reopened.get_feedback(fid).retrieval_context == 'LADC'
    unset = protocol.recall('Ember', inspect_context=True, format='structured')
    assert unset['primary_context'] is None
    assert isinstance(protocol.recall('Ember', format='structured'), list)
    assert 'primary_context' not in reopened.get(plain).data


def test_query_not_modified_or_context_filtered(tmp_path, monkeypatch):
    protocol = MemoryProtocol(EmberDB.connect(str(tmp_path)))
    protocol.remember('Ember reference', primary_context='Pairing Matrix')
    observed = []
    original = protocol.db.search
    def search(query, **kwargs):
        observed.append(query)
        return original(query, **kwargs)
    monkeypatch.setattr(protocol.db, 'search', search)
    protocol.recall('Ember', primary_context='LADC')
    assert observed == ['Ember']


@pytest.mark.parametrize('bad', ['', '  ', [], {}, 7, False])
def test_invalid_context_rejected_before_write(tmp_path, bad):
    db = EmberDB.connect(str(tmp_path))
    p = MemoryProtocol(db)
    before = set(db._store.all_ids())
    with pytest.raises(ValueError): p.remember('x', primary_context=bad)
    with pytest.raises(ValueError): p.recall('x', primary_context=bad)
    with pytest.raises(ValueError): Feedback.from_submission('m', 'a', {'outcome': 'useful', 'retrieval_context': bad})
    assert set(db._store.all_ids()) == before


def test_no_alias_resolution_or_mutation(tmp_path):
    db = EmberDB.connect(str(tmp_path))
    p = MemoryProtocol(db)
    content = {'content': 'Ember', 'primary_context': ' LADC research '}
    rid = p.remember(content)
    assert content == {'content': 'Ember', 'primary_context': ' LADC research '}
    assert db.get(rid).data['primary_context'] == ' LADC research '
    with pytest.raises(ValueError): p.remember(content, primary_context='LADC')


def test_mcp_primary_context_end_to_end(mcp):
    reg = _body(mcp.call_tool('ember_register', {'name': 'context-test'}))
    auth = {'agent_id': reg['agent_id'], 'token': reg['token']}
    ids = []
    for context in ['LADC', 'Pairing Matrix']:
        ids.append(_body(mcp.call_tool('ember_write', dict(content='Ember reference', subject='Ember',
            primary_context=context, **auth)))['id'])
    response = _body(mcp.call_tool('ember_recall', dict(query='Ember', primary_context='LADC', **auth)))
    assert response['primary_context'] == 'LADC'
    assert set(ids) <= {r['id'] for r in response['results']}
    fid = _body(mcp.call_tool('ember_feedback', dict(memory_id=ids[1], outcome='useful',
        retrieval_context=response['primary_context'], **auth)))['feedback_id']
    assert mcp.db.get_feedback(fid).retrieval_context == 'LADC'
    assert _body(mcp.call_tool('ember_read', dict(record_id=ids[1], **auth)))['data']['primary_context'] == 'Pairing Matrix'
    unset = _body(mcp.call_tool('ember_recall', dict(query='Ember', inspect_context=True, **auth)))
    assert unset['primary_context'] is None


def test_rest_primary_context_and_feedback(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from embers import api
    from embers.api import v1, feedback_routes
    db = EmberDB.connect(str(tmp_path))
    monkeypatch.setattr(api, '_get_db', lambda: db)
    protocol = MemoryProtocol(db)
    monkeypatch.setattr(v1, '_proto', lambda _: protocol)
    monkeypatch.setattr(v1, 'require_agent', lambda *_: SimpleNamespace(agent_id='agent'))
    for context in ['LADC', 'Pairing Matrix']:
        asyncio.run(v1.memory_write({'content': 'Ember reference', 'subject': 'Ember', 'primary_context': context}, 'agent', 'token'))
    result = asyncio.run(v1.memory_recall({'query': 'Ember', 'primary_context': 'LADC'}, 'agent', 'token'))
    assert result['primary_context'] == 'LADC'
    assert {r['primary_context'] for r in result['results']} == {'LADC', 'Pairing Matrix'}
    target = next(r['id'] for r in result['results'] if r['primary_context'] == 'Pairing Matrix')
    feedback = asyncio.run(feedback_routes.give_feedback(target, {'outcome': 'useful', 'retrieval_context': 'LADC'}, 'agent', 'token', None))
    assert db.get_feedback(feedback['feedback_id']).retrieval_context == 'LADC'


@pytest.mark.parametrize('format', ['text', 'messages'])
def test_observation_reports_only_rendered_results(tmp_path, format):
    p = MemoryProtocol(EmberDB.connect(str(tmp_path)))
    mid = p.remember('Ember ' + 'x' * 20000, primary_context='Pairing Matrix')
    p.context_builder.max_tokens = 10
    response = p.recall('Ember', primary_context='LADC', format=format)
    assert mid in {r['id'] for r in response['candidates']}
    assert response['results'] == []


def test_v2_feedback_preserves_metadata_without_reinterpreting_scope():
    report = Feedback.from_submission('m', 'a', dict(schema_version=2, channel='relevance',
        outcome='useful', outcome_id='o', context_id='configured-id', context={'task': 'debug'},
        signal=1, retrieval_context='LADC'))
    restored = Feedback.from_dict(report.to_dict())
    assert restored.retrieval_context == 'LADC'
    assert restored.context_id == 'configured-id'
    assert restored.context == {'task': 'debug'}
