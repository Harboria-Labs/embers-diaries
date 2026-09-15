"""Behavioral boundary tests for Rust-owned supersession sidecars."""

from embers import EmberDB, EmberRecord
from embers.engine.writer import VERSION_BACKEND


def test_native_supersession_sidecar_and_chain_are_live(tmp_path):
    assert VERSION_BACKEND == "rust-pyo3"
    db = EmberDB.connect(tmp_path / "store")
    first = EmberRecord(namespace="native-version", data={"value": 1})
    first_id = db.write(first)
    second_id, _ = db.update(first_id, {"value": 2})
    third_id, _ = db.update(second_id, {"value": 3})

    assert db._writer.get_superseded_by(first_id) == second_id
    assert db._writer.get_supersession_chain(first_id) == [
        first_id,
        second_id,
        third_id,
    ]


def test_native_chain_reader_tolerates_a_cycle_like_legacy_python(tmp_path):
    from embers._native import get_supersession_chain, write_supersession

    root = tmp_path / "store"
    root.mkdir()
    write_supersession(str(root), "a", "b", "2026-09-15T00:00:00+00:00")
    write_supersession(str(root), "b", "a", "2026-09-15T00:00:01+00:00")
    assert get_supersession_chain(str(root), "a") == ["a", "b", "a"]


def test_native_cas_resolves_head_hash_and_version(tmp_path):
    from embers._native import resolve_cas_head

    db = EmberDB.connect(tmp_path / "store")
    first_id = db.write(EmberRecord(namespace="cas", data={"value": 1}))
    second_id, _ = db.update(first_id, {"value": 2})
    second = db.get(second_id)

    head_id, actual_hash, version, matches = resolve_cas_head(
        str(db._store.root), first_id, second.content_hash)
    assert head_id == second_id
    assert actual_hash == second.content_hash
    assert version == 2
    assert matches is True

    _, _, _, stale_matches = resolve_cas_head(
        str(db._store.root), first_id, "0" * 64)
    assert stale_matches is False
