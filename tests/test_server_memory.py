from pathlib import Path
import json
import pytest
from embers.db import EmberDB
from embers.integration.server_memory import prepare_memory_services


def test_normal_bootstrap_reuses_identity_and_policy(tmp_path):
    root = tmp_path / 'store'
    db = EmberDB.connect(str(root))
    prepare_memory_services(db, root)
    credentials = tmp_path / 'store-candidate-credentials.json'
    original = credentials.read_text()
    assert ('memories', 'live-test') in db._candidate_services
    before = set(db._store.all_ids())
    other = EmberDB.connect(str(root))
    prepare_memory_services(other, root)
    assert credentials.read_text() == original
    assert set(other._store.all_ids()) == before
    assert other._relevance_services[('memories', 'live-test')].project().generation == 0
    assert credentials.stat().st_mode & 0o077 == 0


def test_bootstrap_does_not_replace_invalid_credentials(tmp_path):
    root = tmp_path / 'store'
    db = EmberDB.connect(str(root))
    prepare_memory_services(db, root)
    credentials = tmp_path / 'store-candidate-credentials.json'
    value = json.loads(credentials.read_text())
    value['token'] = 'invalid'
    credentials.write_text(json.dumps(value))
    before = set(db._store.all_ids())
    with pytest.raises(PermissionError):
        prepare_memory_services(db, root)
    assert set(db._store.all_ids()) == before
