"""
Ember's Diaries — Feature #9 (Sessions)

Behavioral tests mapped to the spec's §9 requirements:

    Session (first-class, bounded period of agent activity)
        * a session is stored as a SESSION record with the §9 shape
          (session_id / agent_id / started_at / ended_at / task / status /
          summary / discoveries / failures / memory_writes)
        * lifecycle active → completed | abandoned is append-only (a transition
          is a NEW version; the history is preserved)
        * a memory is traceable back to the session that produced it

    The system can answer:
        * What did this agent discover during session X?
        * Which memories were created during session X?
        * What failures occurred before memory Y was created?
        * What work led to this memory?

    Plus §15: sessions survive a rebuild-from-store.

Run: diaries/Scripts/python.exe -m pytest tests/test_sessions.py -v
"""

import pytest

from embers import (
    EmberDB, EmberRecord, RecordType, SessionStatus,
)


@pytest.fixture
def db(tmp_path):
    return EmberDB.connect(tmp_path / "session_store")


def _memory(db, content, session_id=None, agent_id=None, namespace="default"):
    return db.write(EmberRecord(
        namespace=namespace, data={"content": content},
        session_id=session_id, agent_id=agent_id, tags=["memory"]))


# ── The §9 shape + lifecycle ────────────────────────────────────────────────────

class TestSessionShape:
    def test_start_session_creates_first_class_record(self, db):
        sid = db.start_session(agent_id="researcher-01", task="find the leak")
        s = db.get_session(sid)
        assert s is not None
        assert s.agent_id == "researcher-01"
        assert s.task == "find the leak"
        assert s.status == SessionStatus.ACTIVE
        assert s.started_at is not None
        assert s.ended_at is None
        assert s.discoveries == [] and s.failures == [] and s.memory_writes == []

        rec = db.get(sid)
        assert rec.record_type == RecordType.SESSION

    def test_end_session_completed(self, db):
        sid = db.start_session(agent_id="a", task="t")
        db.end_session(sid, summary="found and fixed the leak")
        s = db.get_session(sid)
        assert s.status == SessionStatus.COMPLETED
        assert s.summary == "found and fixed the leak"
        assert s.ended_at is not None

    def test_end_session_abandoned(self, db):
        sid = db.start_session(agent_id="a")
        db.end_session(sid, status=SessionStatus.ABANDONED,
                       summary="agent gave up")
        assert db.get_session(sid).status == SessionStatus.ABANDONED

    def test_lifecycle_is_append_only(self, db):
        sid = db.start_session(agent_id="a", task="t")
        db.end_session(sid, summary="done")
        history = db.get_history(sid)
        statuses = [h.data["status"] for h in history]
        assert statuses == ["active", "completed"]


# ── Queries §9 must answer ──────────────────────────────────────────────────────

class TestSessionQueries:
    def test_what_did_this_agent_discover(self, db):
        sid = db.start_session(agent_id="researcher-01")
        d1 = _memory(db, "discovery one", session_id=sid)
        d2 = _memory(db, "discovery two", session_id=sid)
        db.record_discovery(sid, d1)
        db.record_discovery(sid, d2)
        got = [r.id for r in db.session_discoveries(sid)]
        assert set(got) == {d1, d2}

    def test_which_memories_created_during_session(self, db):
        sid = db.start_session(agent_id="a")
        m1 = _memory(db, "m1", session_id=sid)
        m2 = _memory(db, "m2", session_id=sid)
        _other = _memory(db, "unrelated")  # no session
        got = {r.id for r in db.session_memories(sid)}
        assert m1 in got and m2 in got
        assert _other not in got

    def test_memory_traceable_back_to_session(self, db):
        sid = db.start_session(agent_id="a")
        m = _memory(db, "traceable", session_id=sid)
        assert db.get(m).session_id == sid
        assert m in {r.id for r in db.session_memories(sid)}

    def test_what_failures_occurred_before_memory(self, db):
        # §9: "What failures occurred before memory Y was created?"
        sid = db.start_session(agent_id="a")
        f1 = _memory(db, "failure: first attempt crashed", session_id=sid)
        f2 = _memory(db, "failure: second attempt timed out", session_id=sid)
        db.record_failure(sid, f1)
        db.record_failure(sid, f2)
        # The eventual successful memory Y.
        y = _memory(db, "the working fix", session_id=sid)
        db.record_memory_write(sid, y)

        failures = db.session_failures(sid)
        assert {r.id for r in failures} == {f1, f2}
        # All failures were recorded before Y — the session captures the path
        # of failed attempts that preceded the durable memory.
        assert all(f.created_at <= db.get(y).created_at for f in failures)


# ── Curated account de-duplicates ──────────────────────────────────────────────

class TestCuratedAccount:
    def test_record_calls_are_idempotent(self, db):
        sid = db.start_session(agent_id="a")
        d = _memory(db, "d", session_id=sid)
        db.record_discovery(sid, d)
        db.record_discovery(sid, d)
        assert db.get_session(sid).discoveries == [d]

    def test_sessions_filter_by_agent_and_status(self, db):
        s1 = db.start_session(agent_id="researcher-01", task="t1")
        s2 = db.start_session(agent_id="coder-02", task="t2")
        db.end_session(s2, summary="done")

        researchers = db.sessions(agent_id="researcher-01")
        assert [s.session_id for s in researchers] == [s1]

        completed = db.sessions(status=SessionStatus.COMPLETED)
        assert [s.session_id for s in completed] == [s2]

        active = db.sessions(status=SessionStatus.ACTIVE)
        assert s1 in [s.session_id for s in active]
        assert s2 not in [s.session_id for s in active]


# ── Backwards compatibility (§15) ──────────────────────────────────────────────

class TestRebuildFromStore:
    def test_sessions_survive_reload(self, db, tmp_path):
        sid = db.start_session(agent_id="researcher-01", task="persist me")
        m = _memory(db, "m", session_id=sid)
        db.record_memory_write(sid, m)
        db.end_session(sid, summary="wrapped up")

        db2 = EmberDB.connect(tmp_path / "session_store")
        s = db2.get_session(sid)
        assert s is not None
        assert s.status == SessionStatus.COMPLETED
        assert s.task == "persist me"
        assert m in s.memory_writes
        assert m in {r.id for r in db2.session_memories(sid)}
