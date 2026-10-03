"""Managed logical-byte admission under the native backend."""
from concurrent.futures import ThreadPoolExecutor
import pytest
from embers import EmberDB, EmberRecord
from embers._native import atomic_replace, configure_store_quota, store_byte_usage
from embers.config import load_config, ConfigError


def test_record_transaction_refused_before_wal_mutation(tmp_path):
    db = EmberDB.connect(str(tmp_path / 'store'), max_total_bytes=2000)
    before = db._store.wal.path.read_bytes() if db._store.wal.path.exists() else b''
    count = db._store.record_count()
    with pytest.raises(OSError, match='max_total_bytes'):
        db.write(EmberRecord(data={'content': 'x' * 3000}))
    assert db._store.record_count() == count
    after = db._store.wal.path.read_bytes() if db._store.wal.path.exists() else b''
    assert after == before
    assert db.storage_usage()['managed_logical_bytes'] <= 2000


def test_policy_survives_reopen(tmp_path):
    path = tmp_path / 'store'
    EmberDB.connect(str(path), max_total_bytes=2000)
    second = EmberDB.connect(str(path))
    assert second.storage_usage()['max_total_bytes'] == 2000
    with pytest.raises(OSError):
        second.write(EmberRecord(data={'content': 'x' * 3000}))
    with pytest.raises(OSError, match='migration'):
        EmberDB.connect(str(path), max_total_bytes=5000)


def test_replacement_counts_old_and_new_peak(tmp_path):
    root = tmp_path / 'store'
    EmberDB.connect(str(root))
    path = root / 'index-test'
    atomic_replace(str(path), b'a' * 1000)
    used, _ = store_byte_usage(str(root))
    configure_store_quota(str(root), used + 600)
    with pytest.raises(OSError, match='max_total_bytes'):
        atomic_replace(str(path), b'b' * 1000)
    assert path.read_bytes() == b'a' * 1000


def test_concurrent_native_writes_share_admission(tmp_path):
    root = tmp_path / 'store'
    EmberDB.connect(str(root))
    used, _ = store_byte_usage(str(root))
    configure_store_quota(str(root), used + 1600)
    def attempt(name):
        try:
            atomic_replace(str(root / name), b'x' * 1000)
            return True
        except OSError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ['a', 'b']))
    assert sum(results) == 1
    assert store_byte_usage(str(root))[0] <= used + 1600


def test_index_and_annotation_obey_cap(tmp_path):
    root = tmp_path / 'store'
    db = EmberDB.connect(str(root))
    mid = db.write(EmberRecord(data={'content': 'x' * 1000}))
    used, _ = store_byte_usage(str(root))
    configure_store_quota(str(root), used + 200)
    with pytest.raises(OSError, match='max_total_bytes'):
        db._fulltext_index.persist()
    from embers.core.annotation import Annotation
    with pytest.raises(OSError, match='max_total_bytes'):
        db.annotate(mid, Annotation(content='note' * 1000))
    assert db.get(mid) is not None
    assert store_byte_usage(str(root))[0] <= used + 200


def test_config_and_logging_scope(tmp_path):
    cfg = load_config(env={}, overrides={
        'storage.path': str(tmp_path), 'storage.max_total_bytes': 4000,
    })
    assert cfg.storage.max_total_bytes == 4000
    with pytest.raises(ConfigError):
        load_config(env={}, overrides={
            'storage.path': str(tmp_path), 'storage.max_total_bytes': 4000,
            'logging.file': str(tmp_path / 'ember.log'),
        })


def test_symlink_escape_refused(tmp_path):
    root = tmp_path / 'store'
    EmberDB.connect(str(root), max_total_bytes=10000)
    outside = tmp_path / 'outside'
    outside.write_bytes(b'unchanged')
    (root / 'link').symlink_to(outside)
    with pytest.raises(OSError):
        atomic_replace(str(root / 'other'), b'x')
    assert outside.read_bytes() == b'unchanged'


def test_crash_pending_space_cannot_be_consumed_by_another_writer(tmp_path):
    import os
    import subprocess
    import sys
    root = tmp_path / 'store'
    db = EmberDB.connect(str(root), max_total_bytes=12000)
    code = '''
import os, sys
from embers import EmberDB, EmberRecord
db = EmberDB.connect(sys.argv[1])
r = EmberRecord(id="pending-fixture", data={"content": "x" * 2000})
r.seal()
db._store.wal.log("write", r.id, r.to_dict())
os._exit(0)
'''
    subprocess.run([sys.executable, '-c', code, str(root)], check=True)
    used, limit = store_byte_usage(str(root))
    with pytest.raises(OSError, match='max_total_bytes'):
        atomic_replace(str(root / 'steal-recovery-space'), b'x' * (limit - used - 1))
    reopened = EmberDB.connect(str(root))
    assert reopened.get('pending-fixture').data['content'] == 'x' * 2000
    assert reopened.storage_usage()['managed_logical_bytes'] <= limit
