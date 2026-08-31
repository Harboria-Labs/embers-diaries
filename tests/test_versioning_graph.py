"""
Ember's Diaries — Feature #2: Strong Versioning + Causal Graph

Behavioral tests mapped to the spec's §2 questions and §16 → Versioning:

    * an update creates a new version; the previous version stays intact
    * history is NOT assumed linear — a record may be superseded in more than
      one direction (V1 → {V2-A, V2-B}); the branch must be queryable
    * "what came before?" / "what was derived from it?" across BOTH the version
      axis (supersedes) and the derivation axis (derived_from)
    * "what branches exist?" / "what is the current version?"
    * "which memory caused / contributed to another?" (typed causal edges)
    * conflicting claims are mapped, never overwritten (the multi-agent model)
    * the branch-aware version graph survives a checkpoint/reload AND a
      rebuild-from-store, and a pre-Feature-#2 index (no `supersedes` in meta)
      still recovers its lineage from the persisted linear map (§15)

The existing LINEAR retrieval (get_current / get_history / get_at) must keep
working unchanged — those are covered here too, since §2 requires preserving
the existing historical-retrieval functionality.

Run: diaries/Scripts/python.exe -m pytest tests/test_versioning_graph.py -v
"""

import pytest

from embers import EmberDB, EmberRecord
from embers.storage.format import decode_index, encode_index


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    return EmberDB.connect(tmp_path / "ver_store")


def _write(db, content, **kw):
    return db.write(EmberRecord(namespace="v", data={"content": content}, **kw))


def _ids(records):
    return {r.id for r in records}


# ── The linear case still works (backwards compatibility) ─────────────────────

class TestLinearVersioningPreserved:
    def test_update_creates_new_version_old_intact(self, db):
        """§16 Versioning: an update supersedes; the old version is preserved,
        never overwritten, and both still verify their integrity."""
        v1 = _write(db, "v1")
        v2, old = db.update(v1, {"content": "v2"})
        assert old == v1

        # old is hidden by default (superseded) but retrievable and intact
        assert db.get(v1) is None
        old_rec = db.get(v1, include_superseded=True)
        assert old_rec.data["content"] == "v1"
        assert old_rec.verify_integrity() is True

        # new version is the visible one and also intact
        head = db.get(v2)
        assert head.data["content"] == "v2"
        assert head.supersedes == v1
        assert head.verify_integrity() is True

    def test_linear_get_current_and_history_unchanged(self, db):
        """A straight V1→V2→V3 chain resolves exactly as before."""
        v1 = _write(db, "v1")
        v2, _ = db.update(v1, {"content": "v2"})
        v3, _ = db.update(v2, {"content": "v3"})

        assert db.get_current(v1).id == v3
        assert [r.id for r in db.get_history(v1)] == [v1, v2, v3]

    def test_version_ancestors_and_parent_on_linear_chain(self, db):
        v1 = _write(db, "v1")
        v2, _ = db.update(v1, {"content": "v2"})
        v3, _ = db.update(v2, {"content": "v3"})

        assert db.version_parent(v3).id == v2
        assert db.version_parent(v1) is None
        # nearest-first
        assert [r.id for r in db.version_ancestors(v3)] == [v2, v1]
        assert _ids(db.version_descendants(v1)) == {v2, v3}


# ── Branching — the core §2 gap the linear chain could not express ────────────

class TestBranchingVersions:
    def _fork(self, db):
        """V1 superseded in two directions; one branch extends to V3.

              V1
             /  \\
          V2A    V2B
                   \\
                    V3
        """
        v1 = _write(db, "v1")
        v2a, _ = db.update(v1, {"content": "v2a"})
        v2b, _ = db.update(v1, {"content": "v2b"})   # branch: supersede V1 again
        v3, _ = db.update(v2b, {"content": "v3"})
        return v1, v2a, v2b, v3

    def test_a_record_can_be_superseded_in_two_directions(self, db):
        v1, v2a, v2b, v3 = self._fork(db)
        assert _ids(db.version_children(v1)) == {v2a, v2b}
        # each child records V1 as its single version parent
        assert db.version_parent(v2a).id == v1
        assert db.version_parent(v2b).id == v1

    def test_branch_points_identified(self, db):
        v1, v2a, v2b, v3 = self._fork(db)
        assert _ids(db.branch_points(v1)) == {v1}          # only V1 forks
        # querying from anywhere in the lineage finds the same fork
        assert _ids(db.branch_points(v3)) == {v1}

    def test_current_versions_returns_all_live_heads(self, db):
        v1, v2a, v2b, v3 = self._fork(db)
        # the lineage has two unmerged heads: V2A and V3
        assert _ids(db.current_versions(v1)) == {v2a, v3}
        # every head is itself live (not superseded)
        for head in db.current_versions(v1):
            assert db.get(head.id) is not None

    def test_version_tree_captures_full_structure(self, db):
        v1, v2a, v2b, v3 = self._fork(db)
        tree = db.version_tree(v3)      # any node → whole lineage from the root
        assert set(tree[v1]) == {v2a, v2b}
        assert tree[v2a] == []
        assert set(tree[v2b]) == {v3}
        assert tree[v3] == []

    def test_descendants_and_ancestors_span_branches(self, db):
        v1, v2a, v2b, v3 = self._fork(db)
        assert _ids(db.version_descendants(v1)) == {v2a, v2b, v3}
        assert [r.id for r in db.version_ancestors(v3)] == [v2b, v1]
        assert [r.id for r in db.version_ancestors(v2a)] == [v1]

    def test_all_branch_versions_intact_and_verify(self, db):
        """Append-only: forking never mutates a sibling. Every version on every
        branch is still present and passes integrity verification."""
        v1, v2a, v2b, v3 = self._fork(db)
        contents = {}
        for vid in (v1, v2a, v2b, v3):
            r = db.get(vid, include_superseded=True)
            assert r is not None and r.verify_integrity() is True
            contents[vid] = r.data["content"]
        assert contents == {v1: "v1", v2a: "v2a", v2b: "v2b", v3: "v3"}


# ── Derivation axis: derived_from becomes a first-class causal edge ───────────

class TestDerivationGraph:
    def test_what_came_before_spans_version_and_derivation(self, db):
        """'What came before' unifies prior versions AND derivation sources."""
        src_a = _write(db, "source A")
        src_b = _write(db, "source B")
        # a memory derived from two sources...
        derived = _write(db, "synthesis", derived_from=[src_a, src_b])
        # ...then revised (a new version of the synthesis)
        derived_v2, _ = db.update(derived, {"content": "synthesis v2"})

        # before the first synthesis: only its derivation sources
        assert _ids(db.what_came_before(derived)) == {src_a, src_b}
        # before the revision: its prior version (derivation is per-write and
        # not inherited, so v2 has no derived_from of its own)
        assert _ids(db.what_came_before(derived_v2)) == {derived}

    def test_what_was_derived_from_is_the_inverse(self, db):
        src = _write(db, "seed observation")
        d1 = _write(db, "conclusion 1", derived_from=[src])
        d2 = _write(db, "conclusion 2", derived_from=[src])

        assert _ids(db.what_was_derived_from(src)) == {d1, d2}
        # and the descendants side also includes later versions
        d1v2, _ = db.update(d1, {"content": "conclusion 1 revised"})
        assert _ids(db.what_was_derived_from(src)) == {d1, d2}     # direct derivations
        assert _ids(db.what_was_derived_from(d1)) == {d1v2}        # later version

    def test_derived_from_survives_rebuild_from_store(self, tmp_path):
        """The derivation edge is rebuilt from the record on reconnect even
        without a checkpoint."""
        store = tmp_path / "deriv_store"
        db1 = EmberDB.connect(store)
        src = _write(db1, "src")
        d = _write(db1, "derived", derived_from=[src])

        db2 = EmberDB.connect(store)      # no checkpoint → rebuild from .ember
        assert _ids(db2.what_came_before(d)) == {src}
        assert _ids(db2.what_was_derived_from(src)) == {d}


# ── Causal edges: caused_by / led_to / conflicts ──────────────────────────────

class TestCausalEdges:
    def test_caused_by_and_led_to_are_directional(self, db):
        cause = _write(db, "root cause: config drift")
        effect = _write(db, "incident: outage")
        # record the causal link (effect was caused_by cause)
        db.link(effect, cause, "caused_by")
        db.link(cause, effect, "led_to")

        assert _ids(db.caused_by(effect)) == {cause}
        assert _ids(db.led_to(cause)) == {effect}

    def test_conflicts_are_mapped_both_ways_not_overwritten(self, db):
        """The multi-agent model: two agents assert opposite claims. We keep
        BOTH memories and link them `contradicts`; neither is destroyed, and the
        conflict is visible from either side."""
        claim_true = _write(db, "X is true", written_by="agent-A")
        claim_false = _write(db, "X is false", written_by="agent-B")
        db.link(claim_true, claim_false, "contradicts")

        # both memories still exist and verify
        assert db.get(claim_true).verify_integrity() is True
        assert db.get(claim_false).verify_integrity() is True
        # conflict is symmetric
        assert _ids(db.conflicts(claim_true)) == {claim_false}
        assert _ids(db.conflicts(claim_false)) == {claim_true}


# ── Persistence & backwards compatibility of the version graph ────────────────

class TestVersionGraphPersistence:
    def test_branch_graph_survives_checkpoint_and_reload(self, tmp_path):
        store = tmp_path / "persist_store"
        db1 = EmberDB.connect(store)
        v1 = _write(db1, "v1")
        v2a, _ = db1.update(v1, {"content": "v2a"})
        v2b, _ = db1.update(v1, {"content": "v2b"})
        db1.checkpoint()

        db2 = EmberDB.connect(store)
        assert _ids(db2.version_children(v1)) == {v2a, v2b}
        assert _ids(db2.branch_points(v1)) == {v1}

    def test_branch_graph_rebuilds_from_store_without_checkpoint(self, tmp_path):
        store = tmp_path / "rebuild_store"
        db1 = EmberDB.connect(store)
        v1 = _write(db1, "v1")
        v2a, _ = db1.update(v1, {"content": "v2a"})
        v2b, _ = db1.update(v1, {"content": "v2b"})

        db2 = EmberDB.connect(store)      # no checkpoint → rebuild from .ember
        assert _ids(db2.version_children(v1)) == {v2a, v2b}
        assert _ids(db2.current_versions(v1)) == {v2a, v2b}

    def test_pre_feature2_index_recovers_lineage_from_linear_map(self, tmp_path):
        """§15 backwards compatibility: a master index persisted BEFORE Feature
        #2 has no `supersedes` key in any record's meta, but it does carry the
        linear `superseded` map. On load, the version graph must be seeded from
        that map so old (necessarily linear) stores keep their lineage."""
        store = tmp_path / "legacy_store"
        db1 = EmberDB.connect(store)
        v1 = _write(db1, "v1")
        v2, _ = db1.update(v1, {"content": "v2"})
        db1.checkpoint()

        # Simulate a pre-#2 persisted index: strip `supersedes` from every
        # record's meta, leaving only the linear `superseded` map behind.
        master_file = store / "indexes" / "master.json"
        data = decode_index(master_file.read_bytes())
        for meta in data["records"].values():
            meta.pop("supersedes", None)
        assert data.get("superseded", {})        # the linear map is present
        master_file.write_bytes(encode_index(data))

        db2 = EmberDB.connect(store)
        # lineage recovered purely from the linear map
        assert _ids(db2.version_children(v1)) == {v2}
        assert db2.version_parent(v2).id == v1
        assert [r.id for r in db2.version_ancestors(v2)] == [v1]
