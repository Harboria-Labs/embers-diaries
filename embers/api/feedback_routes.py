"""REST parity for feedback, lifecycle, maintenance, session work view."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from ..core.feedback import Feedback, FeedbackOutcome, FeedbackAttribution
from ..maintenance import run_maintenance_cycle

router = APIRouter(prefix="/v1")


def _db():
    from . import _get_db
    return _get_db()


def _agent(db, agent_id, token):
    from . import v1
    return v1.require_agent(db, agent_id, token)


@router.post("/memory/{memory_id}/feedback")
async def give_feedback(
    memory_id: str,
    body: dict,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
    x_ember_session_id: str | None = Header(default=None),
):
    db = _db()
    agent = _agent(db, x_ember_agent_id, x_ember_token)
    outcome = body.get("outcome")
    if not outcome:
        raise HTTPException(400, "outcome required")
    try:
        fb = Feedback(
            memory_id=memory_id,
            agent_id=agent.agent_id,
            outcome=FeedbackOutcome(outcome),
            usefulness=body.get("usefulness"),
            accuracy=body.get("accuracy"),
            attribution=(FeedbackAttribution(body["attribution"])
                         if body.get("attribution") else None),
            note=body.get("note", ""),
            session_id=body.get("session_id") or x_ember_session_id,
        )
        fid = db.give_feedback(memory_id, fb)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"feedback_id": fid}


@router.get("/memory/{memory_id}/feedback")
async def list_feedback(
    memory_id: str,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
):
    db = _db()
    _agent(db, x_ember_agent_id, x_ember_token)
    return {"feedback": [r.data for r in db.feedback_for(memory_id)]}


@router.get("/memory/{record_id}/lifecycle")
async def get_lifecycle(
    record_id: str,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
):
    db = _db()
    _agent(db, x_ember_agent_id, x_ember_token)
    from . import v1
    report = v1._proto(db).get_lifecycle(record_id)
    if report is None:
        raise HTTPException(404, "record not found")
    return report.to_dict()


@router.post("/maintenance")
async def run_maintenance(
    body: dict | None = None,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
):
    db = _db()
    _agent(db, x_ember_agent_id, x_ember_token)
    from . import v1
    namespaces = (body or {}).get("namespaces") or ["memories"]
    return run_maintenance_cycle(v1._proto(db), namespaces)


@router.get("/sessions/{session_id}/work")
async def session_work(
    session_id: str,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
):
    db = _db()
    _agent(db, x_ember_agent_id, x_ember_token)
    session = db.get_session(session_id)
    if session is None:
        raise HTTPException(404, "session not found")
    feedback_ids = [
        r.id for r in db.get_by_session(session_id)
        if getattr(getattr(r, "record_type", None), "value", None) == "feedback"
    ]
    return {
        "task": session.task,
        "agent_id": session.agent_id,
        "memories": list(session.memory_writes),
        "discoveries": list(session.discoveries),
        "failures": list(session.failures),
        "feedback": feedback_ids,
    }
