"""Black-box behavioral checks for Contract 02 on the real native store."""
import hashlib
import json
from pathlib import Path
import pytest
from embers.db import EmberDB
from embers.integration import MemoryProtocol
from embers.core.record import EmberRecord
from embers.core.types import RecordType
from test_feedback_lifecycle import mcp, _body


@pytest.fixture
def fixture(tmp_path):
    db = EmberDB.connect(str(tmp_path / 'store'))
    p = MemoryProtocol(db, default_namespace='orientation')
    a = p.remember({'content': 'latent reactivation query heat old memories', 'subject': 'LADC'}, primary_context='mathematics')
    b = p.remember({'content': 'latent reactivation trial results', 'subject': 'LADC'}, primary_context='experiments')
    c = p.remember({'content': 'implementation checklist', 'subject': 'LADC'}, primary_context='implementation')
    db.link(a, c, label='implementation of equations')
    return db, p, (a, b, c), tmp_path / 'store'


def all_rows(result):
    return [r for s in result['territory'] for c in s['contexts'] for r in c['memories']]


def test_unknown_clues_different_wording_multiple_contexts(fixture):
    db, p, ids, _ = fixture
    out = p.orient('latent reactivation query heat')
    assert out['decision'] == 'agent_selects_context'
    assert out['clues'] == 'latent reactivation query heat'
    assert out['hints'] == {}
    assert out['territory'][0]['subject'] == 'LADC'
    assert {c['primary_context'] for c in out['territory'][0]['contexts']} == {'mathematics', 'experiments', 'implementation'}
    rows = {r['id']: r for r in all_rows(out)}
    assert ids[0] in rows and ids[1] in rows
    assert rows[ids[0]]['signals']['content']['clue_terms'] == ['heat', 'latent', 'query', 'reactivation']
    assert rows[ids[2]]['relationships'][0]['from_id'] == ids[0]
    # The agent can directly reuse the returned label with Contract 01.
    recalled = p.recall('latent', primary_context=rows[ids[0]]['primary_context'], format='structured')
    assert recalled['primary_context'] == 'mathematics'


def test_no_mutation_duplication_or_access_reinforcement(fixture):
    db, p, _, root = fixture
    def snapshot():
        return {str(f.relative_to(root)): hashlib.sha256(f.read_bytes()).hexdigest()
                for f in root.rglob('*') if f.is_file()}
    before = snapshot()
    p.orient('latent heat')
    p.orient('latent heat')
    assert snapshot() == before


def test_empty_result_and_unset_context(fixture):
    db, p, _, _ = fixture
    rid = p.remember('forgotten cue', tags=['orientation-tag'])
    out = p.orient('forgotten')
    assert out['territory'][0]['subject'] is None
    assert out['territory'][0]['contexts'][0]['primary_context'] is None
    assert out['returned_ids'] == [rid]
    assert p.orient('zzzzneverstored')['territory'] == []


def test_ablation_and_hints(fixture):
    db, p, ids, _ = fixture
    assert p.orient('latent', signals=['subject'])['returned_ids'] == []
    out = p.orient('latent', signals=['content'])
    assert ids[2] not in out['candidate_ids']  # relation expansion disabled
    hinted = p.orient('unknownword', hints={'subject': 'LADC'}, signals=['subject'])
    assert set(ids) <= set(hinted['returned_ids'])
    assert all(r['signals']['subject']['hint_terms'] == ['ladc'] for r in all_rows(hinted))
    tagged = p.remember('other', tags=['ambertag'], primary_context='separate')
    assert p.orient('ambertag', signals=['tags'])['returned_ids'] == [tagged]
    assert tagged in p.orient('separate', signals=['primary_context'])['returned_ids']


def test_relationships_are_one_hop_and_namespace_scoped(fixture):
    db, p, (a, b, c), _ = fixture
    d = p.remember('unrelated next hop', primary_context='next')
    outside = db.write(EmberRecord(namespace='private', data={'content': 'secret'}))
    db.link(c, d)
    db.link(a, outside)
    out = p.orient('query heat')
    assert c in out['returned_ids']
    assert d not in out['candidate_ids'] and outside not in out['candidate_ids']
    assert 'secret' not in json.dumps(out)
    reverse = p.orient('checklist')
    assert a not in reverse['candidate_ids']  # no invented reverse edge


def test_large_matching_set_all_response_bounds(fixture):
    db, p, _, _ = fixture
    for i in range(140):
        p.remember({'content': 'latent ' + 'é' * 1200, 'subject': f'subject-{i % 7}'}, primary_context=f'context-{i % 13}')
    out = p.orient('latent', limits=dict(subjects=2, contexts=3, memories_per_context=2,
        records=4, candidates=25, relationships=2, preview_chars=80, response_bytes=4096))
    rows = all_rows(out)
    assert len(out['territory']) <= 2
    assert sum(len(s['contexts']) for s in out['territory']) <= 3
    assert all(len(c['memories']) <= 2 for s in out['territory'] for c in s['contexts'])
    assert len(rows) <= 4 and len(out['candidate_ids']) <= 25
    assert out['diagnostics']['edges_examined'] <= 2
    assert all(len(r['preview']) <= 80 for r in rows)
    assert len(json.dumps(out, ensure_ascii=False, separators=(',', ':')).encode()) == out['response_bytes'] <= 4096
    assert out['diagnostics']['shortlist_capped']


def test_restart_uses_persisted_memories_and_graph(fixture):
    db, p, ids, root = fixture
    before = p.orient('latent reactivation')
    reopened = MemoryProtocol(EmberDB.connect(str(root)), default_namespace='orientation')
    after = reopened.orient('latent reactivation')
    assert after == before
    assert ids[2] in after['returned_ids']


@pytest.mark.parametrize('kwargs', [dict(clues=''), dict(clues='x' * 2049), dict(clues='x', hints={'other': 'y'}),
    dict(clues='x', limits={'records': True}), dict(clues='x', limits={'records': 1000}),
    dict(clues='x', limits={'unknown': 2}), dict(clues='x', signals=['relations']),
    dict(clues='x', signals=['content', 'content']), dict(clues='x', signals=['madeup'])])
def test_invalid_requests(fixture, kwargs):
    with pytest.raises(ValueError): fixture[1].orient(**kwargs)


def test_internal_records_and_oversize_labels_not_exposed(fixture):
    db, p, _, _ = fixture
    internal = db.write(EmberRecord(namespace='orientation', record_type=RecordType.RAW, data={'content': 'hiddenmetadata'}))
    assert p.orient('hiddenmetadata')['returned_ids'] == []
    rid = p.remember({'content': 'hugecontext', 'subject': 'x' * 257})
    out = p.orient('hugecontext')
    assert out['returned_ids'] == [] and out['diagnostics']['skipped_oversize_labels'] == 1


def test_mcp_schema_and_calls(mcp):
    from embers.mcp.server import TOOLS
    assert any(t['name'] == 'ember_orient' for t in TOOLS)
    reg = _body(mcp.call_tool('ember_register', {'name': 'orientation'}))
    auth = {'agent_id': reg['agent_id'], 'token': reg['token']}
    rid = _body(mcp.call_tool('ember_write', dict(content='latent reactivation', subject='LADC', primary_context='mathematics', **auth)))['id']
    result = _body(mcp.call_tool('ember_orient', dict(clues='latent', **auth)))
    assert result['returned_ids'] == [rid]
    assert mcp.call_tool('ember_orient', dict(clues='latent', **dict(auth, token='invalid')))['isError']


def test_rest_orientation_parity_and_invalid_limits(fixture, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from fastapi import HTTPException
    from embers import api
    from embers.api import v1
    db, p, _, _ = fixture
    monkeypatch.setattr(api, '_get_db', lambda: db)
    monkeypatch.setattr(v1, '_proto', lambda _: p)
    monkeypatch.setattr(v1, 'require_agent', lambda *_: SimpleNamespace(agent_id='reader'))
    result = asyncio.run(v1.memory_orient({'clues': 'latent'}, 'reader', 'token'))
    assert result == p.orient('latent')
    with pytest.raises(HTTPException) as error:
        asyncio.run(v1.memory_orient({'clues': 'latent', 'limits': {'records': 0}}, 'reader', 'token'))
    assert error.value.status_code == 400


def test_mcp_private_namespace_is_denied(mcp):
    reg = _body(mcp.call_tool('ember_register', {'name': 'unprivileged'}))
    mcp.db.create_namespace('secret', owner='another-agent')
    mcp.protocol.remember('latent secret', namespace='secret')
    result = mcp.call_tool('ember_orient', dict(clues='latent', namespace='secret',
        agent_id=reg['agent_id'], token=reg['token']))
    assert result['isError']
    assert 'latent secret' not in json.dumps(result)


def test_ranking_exposes_clue_counts_and_stable_id_ties(fixture):
    db, p, ids, _ = fixture
    out = p.orient('query heat latent', signals=['content'])
    assert out['returned_ids'][0] == ids[0]
    first = all_rows(out)[0]
    assert first['ranking'] == {'unique_clue_terms': 3, 'hint_terms': 0, 'matching_fields': 1}


def test_deprecated_and_superseded_not_candidates(fixture):
    from embers.core.types import DeprecationReason
    db, p, ids, _ = fixture
    db.deprecate(ids[0], DeprecationReason.MANUAL, 'test')
    old = db.get(ids[1])
    newer, _ = db.update(ids[1], dict(old.data, content='latent replacement'), expected_hash=old.content_hash)
    out = p.orient('latent', signals=['content'])
    assert ids[0] not in out['returned_ids'] and ids[1] not in out['returned_ids']
    assert newer in out['returned_ids']
