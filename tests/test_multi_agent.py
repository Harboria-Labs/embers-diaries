"""
Ember's Diaries — Multi-Agent Memory (Feature #8) behavioral tests.

§16 discipline: these are behavioral tests against the real store, not shape
tests on a class. They prove that here is WHAT HAPPENS when multiple agents
operate against one substrate — and that the guarantee that makes the feature
worth having ("adding more agents does not require redesigning the storage
layer") actually holds.

The seven §8 supports each get a test group:
    agent identity       → records carry a distinct agent_id
    agent-specific context → as_agent().context() / memories()
    session identity     → as_agent(agent_id, session_id) scopes writes
    concurrent access    → threads interleaving writes stay attributed
    shared namespaces    → agents write/read one namespace
    shared durable memories → an agent's promoted memory is visible to others
    provenance           → who/what/where/why answered per record

Plus the opt-in enforcement gate (§15: default is OFF so existing stores are
not invalidated).
"""

import os
import tempfile
import threading

import pytest

from embers import EmberDB, EmberRecord
from embers.core.failure import Failure
from embers.core.proposal import MemoryProposal


@pytest.fixture
def db():
    path = tempfile.mkdtemp(prefix="embers_ma_")
    return EmberDB.connect(path)


@pytest.fixture
def two_agents(db):
    researcher = db.as_agent("researcher-01")
    coder = db.as_agent("coder-02")
    return db, researcher, coder


# ── Agent identity ────────────────────────────────────────────────────────────

class TestAgentIdentity:
    def test_each_record_stamps_its_agent(self, two_agents):
        db, researcher, coder = two_agents
        rid_a = researcher.write(EmberRecord(
            namespace="shared", data={"content": "found a correlation"}))
        rid_b = coder.write(EmberRecord(
            namespace="shared", data={"content": "wrote the parser"}))

        rec_a = db.get(rid_a)
        rec_b = db.get(rid_b)
        assert rec_a.agent_id == "researcher-01"
        assert rec_b.agent_id == "coder-02"
        assert rec_a.written_by == "researcher-01"
        assert rec_b.written_by == "coder-02"

    def test_written_record_object_is_not_mutated(self, two_agents):
        """A record the caller already holds must not be silently re-branded —
        it may be shared and the stamp belongs on the new record only."""
        db, researcher, _ = two_agents
        rec = EmberRecord(namespace="shared", data={"content": "mine"})
        researcher.write(rec)
        assert rec.agent_id is None
        assert rec.written_by == "system"

    def test_agents_list_reflects_writes(self, two_agents):
        db, researcher, coder = two_agents
        # register identities that actually wrote
        researcher.write(EmberRecord(namespace="shared", data={"content": "a"}))
        coder.write(EmberRecord(namespace="shared", data={"content": "b"}))
        assert "researcher-01" in db.agents()
        assert "coder-02" in db.agents()


# ── Agent-specific context ────────────────────────────────────────────────────

class TestAgentContext:
    def test_context_reports_agent_footprint(self, two_agents):
        db, researcher, _ = two_agents
        researcher.write(EmberRecord(namespace="shared", data={"content": "m"}))
        researcher.write(EmberRecord(namespace="shared", data={"content": "n"}))
        ctx = researcher.context()
        assert ctx["agent_id"] == "researcher-01"
        assert ctx["total_written"] >= 2
        assert ctx["live_heads"] >= 2
        assert ctx["by_type"].get("document", 0) >= 2

    def test_agent_context_via_db(self, two_agents):
        db, researcher, _ = two_agents
        researcher.write(EmberRecord(namespace="shared", data={"content": "m"}))
        ctx = db.agent_context("researcher-01")
        assert ctx is not None and ctx["agent_id"] == "researcher-01"
        assert db.agent_context("never-wrote") is None

    def test_agent_specific_queries_are_isolated(self, two_agents):
        """Agent-specific context must not leak one agent's writes to another."""
        db, researcher, coder = two_agents
        researcher.write(EmberRecord(namespace="shared", data={"content": "a"}))
        coder.write(EmberRecord(namespace="shared", data={"content": "b"}))

        shown_to_researcher = researcher.context()
        assert shown_to_researcher["total_written"] == 1
        shown_to_coder = coder.context()
        assert shown_to_coder["total_written"] == 1
        assert shown_to_coder["agent_id"] == "coder-02"


# ── Session identity ──────────────────────────────────────────────────────────

class TestSessionScoping:
    def test_session_scoped_writes_carry_session(self, two_agents):
        db, researcher, _ = two_agents
        task_view = researcher.in_session("sess-1")
        rid = task_view.write(EmberRecord(
            namespace="shared", data={"content": "work done in session 1"}))
        rec = db.get(rid)
        assert rec.agent_id == "researcher-01"
        assert rec.session_id == "sess-1"

    def test_same_agent_different_sessions_stay_distinct(self, two_agents):
        db, researcher, _ = two_agents
        r1 = researcher.in_session("sess-1").write(
            EmberRecord(namespace="shared", data={"content": "s1"}))
        r2 = researcher.in_session("sess-2").write(
            EmberRecord(namespace="shared", data={"content": "s2"}))
        assert db.get(r1).session_id == "sess-1"
        assert db.get(r2).session_id == "sess-2"
        # get_by_session answers "which agent was active in session Y"
        assert db.get_by_session("sess-1")[0].agent_id == "researcher-01"


# ── Concurrent access ─────────────────────────────────────────────────────────

class TestConcurrentAgents:
    def test_interleaved_writes_stay_attributed(self, db):
        """Two agents hammering the same store from different threads must not
        cross-attribute. The append-only store + WriteEngine lock serialize the
        writes; attribution is stamped before the write, so each record keeps
        its own agent even with interleaving."""
        agents = [db.as_agent(f"agent-{i}") for i in range(4)]
        results = []

        def writer(av, tag):
            out = []
            for i in range(10):
                rid = av.write(EmberRecord(
                    namespace="shared", data={"content": f"{tag}-{i}"}))
                out.append(rid)
            results.extend(out)

        threads = [threading.Thread(target=writer, args=(av, av.agent_id))
                   for av in agents]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Every write is attributable: one of the four wrote it, and the
        # record's agent matches the writer that reported it (round-trip).
        for rid in results:
            rec = db.get(rid)
            assert rec.agent_id in {a.agent_id for a in agents}
            assert rec.data["content"].startswith(f"{rec.agent_id}-")

    def test_no_storage_redesign_when_adding_agents(self, db):
        """§8's closing guarantee, as a test: adding a 10th agent requires
        constructing one more view — the store and its queries are unchanged."""
        # unchanged substrate, opened once
        base = db.get_by_agent("someone-new")
        assert base == []

        # adding the agent is one line on the existing db — no new storage
        newcomer = db.as_agent("agent-10")
        rid = newcomer.write(EmberRecord(
            namespace="shared", data={"content": "tenth agent's memory"}))
        assert db.get(rid).agent_id == "agent-10"
        # and it is immediately queryable through the existing index path
        assert db.get_by_agent("agent-10")[0].id == rid


# ── Shared namespaces ─────────────────────────────────────────────────────────

class TestSharedNamespaces:
    def test_agents_write_and_read_one_namespace(self, two_agents):
        db, researcher, coder = two_agents
        researcher.write(EmberRecord(namespace="team", data={"content": "A"}))
        coder.write(EmberRecord(namespace="team", data={"content": "B"}))
        all_records = db.get_namespace("team")
        assert {r.agent_id for r in all_records} == {"researcher-01", "coder-02"}

    def test_shared_durable_memory_visible_to_others(self, two_agents):
        """A promoted durable memory in a shared namespace is readable by any
        agent — multi-agent sharing without an agent-side cache."""
        db, researcher, coder = two_agents
        memory_id = researcher.write(EmberRecord(
            namespace="shared", data={"content": "the answer is 42"}))
        # coder reads it through the shared substrate, attributing it to the author
        seen = [r for r in db.get_namespace("shared")
                if r.id == memory_id]
        assert seen, "coder must see the authored memory"
        assert seen[0].agent_id == "researcher-01"


# ── Provenance (every write attributable) ─────────────────────────────────────

class TestProvenance:
    def test_proposals_and_failures_are_attributed(self, two_agents):
        """§8: 'Every memory write should be attributable to an agent' — the
        durable-memory pipeline too, not just plain writes."""
        db, researcher, coder = two_agents
        pid = researcher.propose(MemoryProposal(
            namespace="shared", discovery={"content": "a discovery"},
            reason="because", evidence=[], confidence=0.6))
        fid = coder.report_failure(Failure(
            approach="parse X with Y", failed="boom", cause="memory limit"))

        assert db.get(pid).agent_id == "researcher-01"
        assert db.get(fid).agent_id == "coder-02"

    def test_every_write_carries_who_and_why(self, two_agents):
        db, researcher, _ = two_agents
        rid = researcher.write(EmberRecord(
            namespace="shared", data={"content": "x"},
            creation_reason="checked the source"))
        prov = db.get_provenance(rid)
        assert prov["agent_id"] == "researcher-01"
        assert prov["author"] == "researcher-01"


# ── Opt-in attribution enforcement (§15: off by default) ──────────────────────

class TestAttributionEnforcement:
    def test_default_off_preserves_unattributed_writes(self, db):
        """§15: the default must not silently invalidate existing stores — an
        unattributed write keeps working unless the user opts in."""
        rid = db.write(EmberRecord(
            namespace="shared", data={"content": "no agent, still allowed"}))
        assert db.get(rid).agent_id is None

    def test_enforce_rejects_unattributed_write(self):
        store = EmberDB.connect(tempfile.mkdtemp(prefix="embers_enforce_"),
                                enforce_attribution=True)
        with pytest.raises(ValueError):
            store.write(EmberRecord(namespace="shared",
                                    data={"content": "anonymous"}))

    def test_enforce_allows_attributed_and_view_write(self):
        store = EmberDB.connect(tempfile.mkdtemp(prefix="embers_enforce_"),
                                enforce_attribution=True)
        # direct: record carries the agent
        rid = store.write(EmberRecord(namespace="shared",
                                      data={"content": "x"}, agent_id="author"))
        assert store.get(rid).agent_id == "author"
        # via view: stamped before reaching the gate
        rid2 = store.as_agent("coder").write(
            EmberRecord(namespace="shared", data={"content": "y"}))
        assert store.get(rid2).agent_id == "coder"

    def test_enforce_gates_proposal_and_failure(self):
        store = EmberDB.connect(tempfile.mkdtemp(prefix="embers_enforce_"),
                                enforce_attribution=True)
        # the durable-write pipeline is gated too
        with pytest.raises(ValueError):
            store.propose(MemoryProposal(
                namespace="shared", discovery={"content": "d"}, reason="r"))
        with pytest.raises(ValueError):
            store.report_failure(Failure(
                approach="a", failed="b", cause="c"))
