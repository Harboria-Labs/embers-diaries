"""Behavioral coverage for EmberDB.audit_write().

The audit is a read-only composition of existing immutable records, provenance,
version indexes, causal graph edges, proposals, and Evidence. It reports stored
facts and concise stored reasons; it never manufactures a reasoning transcript.
"""

import pytest

from embers import (
    DeprecationReason,
    EdgeType,
    EmberDB,
    EmberRecord,
    Evidence,
    MemoryProposal,
    RecordType,
    SourceType,
)


@pytest.fixture
def db(tmp_path):
    return EmberDB.connect(tmp_path / "audit_store")


def _write(db, content, **kwargs):
    return db.write(EmberRecord(
        namespace="audit",
        record_type=RecordType.NODE,
        data={"content": content},
        **kwargs,
    ))


def _ids(refs):
    return {ref["id"] for ref in refs}


def test_audit_promoted_revision_traces_origin_evidence_and_provenance(db):
    source_id = _write(db, "benchmark output", agent_id="runner")
    evidence = Evidence(
        source="tool://pytest/run-17",
        source_type=SourceType.EXPERIMENTALLY_VERIFIED,
        reference="artifact://report.json",
        description="focused tests passed",
        agent_id="runner",
        session_id="session-discovery",
    )
    proposal_id = db.propose(MemoryProposal(
        namespace="audit",
        discovery={"content": "candidate conclusion"},
        reason="benchmark result is reproducible",
        evidence=[evidence],
        derivation=[source_id],
        written_by="researcher",
        agent_id="researcher",
        session_id="session-discovery",
    ))
    memory_id, _ = db.promote(proposal_id, validated_by="reviewer")
    memory = db.get(memory_id)

    revision_id, _ = db.update(
        memory_id,
        {"content": "reviewed conclusion"},
        written_by="editor",
        agent_id="editor-2",
        session_id="session-review",
        creation_reason="incorporate review",
        derived_from=[source_id],
    )
    outcome_id = _write(db, "downstream outcome")
    derived_id = _write(db, "later synthesis", derived_from=[revision_id])
    assert db.link(revision_id, outcome_id, EdgeType.LED_TO.value)

    audit = db.audit_write(revision_id)

    assert audit["record"] == {
        "id": revision_id,
        "record_type": "node",
        "namespace": "audit",
        "version": 2,
        "content_hash": db.get(revision_id).content_hash,
        "parent_hash": memory.content_hash,
        "created_at": db.get(revision_id).created_at.isoformat(),
    }
    assert audit["provenance"] == {
        "author": "editor",
        "agent_id": "editor-2",
        "session_id": "session-review",
        "timestamp": db.get(revision_id).created_at.isoformat(),
        "creation_reason": "incorporate review",
        "source": None,
        "derived_from": [source_id],
    }
    assert audit["version_context"]["root_id"] == memory_id
    assert audit["version_context"]["previous_version"]["id"] == memory_id
    assert audit["version_context"]["previous_version"]["content_hash"] == memory.content_hash
    assert audit["version_context"]["next_versions"] == []
    assert audit["state"] == {
        "result": "current",
        "is_lineage_head": True,
        "superseded": False,
        "superseded_by": None,
        "deprecated": False,
    }

    assert _ids(audit["causal_context"]["derived_from"]) == {source_id}
    assert _ids(audit["causal_context"]["derived_records"]) == {derived_id}
    assert _ids(audit["causal_context"]["led_to"]) == {outcome_id}
    assert {edge["edge_type"] for edge in audit["causal_context"]["edges"]} >= {
        EdgeType.DERIVED_FROM.value,
        EdgeType.LED_TO.value,
    }

    proposal = audit["proposal"]
    assert proposal["proposal_id"] == proposal_id
    assert proposal["status"] == "promoted"
    assert proposal["promoted_to"] == memory_id
    assert proposal["discovery"] == {"content": "candidate conclusion"}
    assert proposal["reason"] == "benchmark result is reproducible"
    assert proposal["evidence"][0]["evidence_id"] == evidence.evidence_id

    # Evidence attached to the promoted root remains visible while the exact
    # target version is explicit; it is not falsely relabeled as revision proof.
    assert len(audit["evidence"]) == 1
    traced = audit["evidence"][0]
    assert traced["supports_record_id"] == memory_id
    assert traced["record"]["id"] == evidence.evidence_id
    assert traced["evidence"]["source"] == "tool://pytest/run-17"
    assert traced["evidence"]["content_hash"] == evidence.content_hash


def test_audit_reports_full_branch_and_deprecation_state(db):
    root_id = _write(db, "root")
    child_a, _ = db.update(root_id, {"content": "branch a"})
    child_b, _ = db.update(root_id, {"content": "branch b"})
    assert db.deprecate(
        root_id, DeprecationReason.INVALID, "bad premise", "auditor")

    audit = db.audit_write(root_id)

    assert audit["state"]["result"] == "branched_and_deprecated"
    assert audit["state"]["deprecated"] is True
    assert audit["state"]["superseded"] is True
    assert audit["state"]["superseded_by"] == child_b
    assert _ids(audit["version_context"]["next_versions"]) == {child_a, child_b}
    assert _ids(audit["version_context"]["lineage_heads"]) == {child_a, child_b}
    assert _ids(audit["version_context"]["branch_points"]) == {root_id}
    assert set(audit["version_context"]["tree"][root_id]) == {child_a, child_b}


def test_audit_of_proposal_reports_current_result_without_private_reasoning(db):
    proposal_id = db.propose(MemoryProposal(
        namespace="audit",
        discovery="observable claim",
        reason="concise stored reason",
    ))
    rejected_id = db.reject(proposal_id, reason="measurement contradicted it")

    audit = db.audit_write(proposal_id)

    assert audit["record"]["id"] == proposal_id
    assert audit["state"]["result"] == "superseded"
    assert audit["version_context"]["next_versions"][0]["id"] == rejected_id
    assert audit["proposal"]["record"]["id"] == rejected_id
    assert audit["proposal"]["status"] == "rejected"
    assert audit["proposal"]["rejection_reason"] == "measurement contradicted it"
    assert audit["proposal"]["discovery"] == "observable claim"
    assert set(audit["provenance"]) == {
        "author", "agent_id", "session_id", "timestamp",
        "creation_reason", "source", "derived_from",
    }
    assert "chain_of_thought" not in repr(audit)


def test_audit_is_read_only_and_unknown_ids_return_none(db):
    record_id = _write(db, "do not mutate", agent_id="writer")
    store_root = db._store.root

    before = {
        path.relative_to(store_root).as_posix(): path.read_bytes()
        for path in store_root.rglob("*") if path.is_file()
    }
    audit = db.audit_write(record_id)
    missing = db.audit_write("does-not-exist")
    after = {
        path.relative_to(store_root).as_posix(): path.read_bytes()
        for path in store_root.rglob("*") if path.is_file()
    }

    assert audit["record"]["id"] == record_id
    assert audit["proposal"] is None
    assert audit["evidence"] == []
    assert missing is None
    assert after == before
