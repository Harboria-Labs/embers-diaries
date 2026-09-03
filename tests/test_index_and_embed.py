"""Regressions: MasterIndex wired into ReadEngine, first-write TF-IDF not zeros."""

from pathlib import Path

from embers import EmberDB, EmberRecord
from embers.integration.embeddings import EmbeddingPipeline
from embers.integration.memory_protocol import MemoryProtocol


def test_reader_receives_master_index(tmp_path: Path):
    db = EmberDB.connect(str(tmp_path / "store"))
    assert db._reader._master_index is db._master_index

    a = db.write(EmberRecord(namespace="alpha", data={"content": "only in alpha"}))
    b = db.write(EmberRecord(namespace="beta", data={"content": "only in beta"}))

    alpha = db.get_namespace("alpha")
    beta = db.get_namespace("beta")
    assert {r.id for r in alpha} == {a}
    assert {r.id for r in beta} == {b}


def test_first_tfidf_embed_is_not_zero_vector():
    pipe = EmbeddingPipeline(dimension=256)
    rec = EmberRecord(
        namespace="memories",
        data={"content": "The user prefers dark mode for the interface."},
        tags=["dark-mode", "interface"],
    )
    vec = pipe.embed_record(rec)
    assert len(vec) == 256
    assert any(x != 0.0 for x in vec)
    q = pipe.embed_text("What does this user prefer about the interface?")
    assert any(x != 0.0 for x in q)


def test_remember_first_write_embedding_not_zero(tmp_path: Path):
    db = EmberDB.connect(str(tmp_path / "store"))
    proto = MemoryProtocol(db)
    rid = proto.remember("The user prefers dark mode for the interface.")
    rec = db.get(rid)
    assert rec.embedding
    assert any(x != 0.0 for x in rec.embedding)
