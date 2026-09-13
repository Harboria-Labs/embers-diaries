"""Enums available after bind; REST matches MCP for feedback/lifecycle."""

import os
import tempfile

import pytest

import embers
from embers.core.types import RecordType, EdgeType


def test_feedback_enums_are_real():
    assert getattr(RecordType, "FEEDBACK").value == "feedback"
    assert getattr(EdgeType, "FEEDBACK_ON").value == "feedback_on"


def test_v1_feedback_and_lifecycle():
    pytest.importorskip("fastapi")
    os.environ["EMBER_STORE"] = tempfile.mkdtemp(prefix="embers_thin_")
    from tests.test_v1_promotion import ASGIClient
    from embers.api import app
    client = ASGIClient(app)
    reg = client.post("/v1/agents/register",
                      json={"name": "thin", "provider": "local", "model": "t"})
    assert reg.status_code == 200, reg.text
    auth = {
        "X-Ember-Agent-Id": reg.json()["agent_id"],
        "X-Ember-Token": reg.json()["token"],
    }
    written = client.post("/v1/memory/write", headers=auth,
                          json={"content": "used later", "room": "task"})
    assert written.status_code == 200, written.text
    mid = written.json()["id"]
    fb = client.post(f"/v1/memory/{mid}/feedback", headers=auth,
                     json={"outcome": "useful"})
    assert fb.status_code == 200, fb.text
    listed = client.get(f"/v1/memory/{mid}/feedback", headers=auth)
    assert listed.status_code == 200
    assert listed.json()["feedback"]
    life = client.get(f"/v1/memory/{mid}/lifecycle", headers=auth)
    assert life.status_code == 200
    assert "state" in life.json()
