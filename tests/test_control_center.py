"""Control shell grants no human authority; readers retain namespace ACLs."""
import hashlib
import pytest
from fastapi import FastAPI
from embers import EmberDB, MemoryProposal, Evidence, AccessLevel
from embers.config import load_config, ConfigError
from embers.api.control import install
from tests.test_v1_promotion import ASGIClient

@pytest.mark.parametrize('path',['/v1','/observer','/research','/health','/x/y','//evil','/UP','/x?y'])
def test_bad_control_paths(path):
    with pytest.raises(ConfigError):
        load_config(env={}, overrides={'api.control_path':path})

def test_custom_route_and_denied_actions():
    config=load_config(env={'EMBER_CONTROL_PATH':'/quiet-room'})
    app=FastAPI();install(app,config.api.control_path);client=ASGIClient(app)
    page=client.get('/quiet-room')
    assert page.status_code==200 and '/quiet-room/control.js' in page.text
    assert client.get('/control').status_code==404
    assert client.post('/quiet-room/decision',json={}).status_code==403
    assert client.post('/quiet-room/configuration',json={}).status_code==403
    assert client.get('/quiet-room/state',params={'namespace':'private'}).status_code==401

def test_authorized_state_is_readonly_and_not_human_authority(tmp_path,monkeypatch):
    import embers.api as api
    import embers.api.v1 as v1
    db=EmberDB.connect(tmp_path/'store');monkeypatch.setattr(api,'_db',db)
    monkeypatch.setattr(v1,'_registry',None);monkeypatch.setattr(v1,'_protocol',None)
    client=ASGIClient(api.app)
    reg=client.post('/v1/agents/register',json={'name':'reader'}).json()
    headers={'X-Ember-Agent-Id':reg['agent_id'],'X-Ember-Token':reg['token']}
    pid=db.propose(MemoryProposal(namespace='public',discovery='unverified claim',
        evidence=[Evidence(reference='test')],agent_id=reg['agent_id']))
    def hashes():
        return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in db._store.records_dir.glob('*.ember')}
    db.create_namespace('private', access_level=AccessLevel.PRIVATE, owner='other')
    assert client.get('/control/state',headers=headers,params={'namespace':'private'}).status_code==403
    before=hashes()
    result=client.get('/control/state',headers=headers,params={'namespace':'public'})
    assert result.status_code==200,result.text
    row=result.json();assert row['human_admin'] is False
    assert row['proposals'][0]['proposal_id']==pid
    assert row['research_settings']['configuration_history']==[]
    assert hashes()==before
    assert client.get('/control/state',headers={'Authorization':'Bearer observer-code'},params={'namespace':'public'}).status_code==401
    assert client.post('/control/decision',headers=headers,json={'proposal_id':pid}).status_code==403
    assert client.post('/control/configuration',headers=headers,json={'config':{}}).status_code==403
    assert hashes()==before
