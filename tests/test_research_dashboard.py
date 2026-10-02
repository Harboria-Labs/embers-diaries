"""New presentation routes remain observational, no engine or configuration writes."""
import hashlib
from pathlib import Path
from fastapi.testclient import TestClient
from embers import api
from embers.api import observer_build
from embers.db import EmberDB
from embers.integration import MemoryProtocol
from embers.integration.usefulness_service import enable,service
from embers.integration.research_observer import issue,page


def test_pages_health_build_and_observer_are_readonly(tmp_path,monkeypatch):
    db=EmberDB.connect(str(tmp_path/'store'));enable(db,{'admin'})
    proto=MemoryProtocol(db,default_namespace='ui-check')
    rid=proto.remember('unchanged memory')
    service(db,'ui-check').apply('report',dict(target=dict(kind='memory',memory_ids=[rid]),context='test',feedback_type='CONTRIBUTED'),actor='admin',request_id='one')
    grant=issue(db,'admin')
    monkeypatch.setattr(api,'_get_db',lambda:db)
    def hashes():return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob('*') if p.is_file()}
    before=hashes();client=TestClient(api.app)
    for path in ['/visualizer','/observer','/status','/health','/v1/observer/build']:
        assert client.get(path).status_code==200
    page(db,grant['code'])
    assert hashes()==before


def test_shared_presentation_and_unchanged_transport():
    for ns in [True,False]:
        html=observer_build.render(namespace=ns)
        assert '__RESEARCH_DASHBOARD__' not in html
        assert 'window.EmberResearchUI=' in html
        assert 'Memory Flow' in html and 'Pairing Matrix' in html
        assert 'observer/transport.js?v=' in html
    assert observer_build.TRANSPORT_SHA256=='b7f69d4f06405b066fee1e65e673100665d771131e13de58e7b03b1e2f0593c0'
    source=Path('embers/api/research_dashboard.js').read_text()
    assert "fetch('/health'" in source
    assert "method:'POST'" not in source and 'fixture()' not in source


def test_settings_wording_is_current():
    schema=Path('embers/integration/research_schema.py').read_text()
    page=Path('embers/api/research_settings.html').read_text()
    assert 'W uncoupled' not in schema and 'W does not expand retrieval' not in schema
    assert 'does not use it to expand retrieval' not in page
    assert 'Pairing Matrix V1' in schema and 'at most one paired memory' in page
