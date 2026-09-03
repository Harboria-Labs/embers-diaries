"""Behavioral tests for agent registration (spec §21)."""

from pathlib import Path

import pytest

from embers import EmberDB
from embers.identity import AgentRegistry


@pytest.fixture
def db(tmp_path: Path):
    return EmberDB.connect(str(tmp_path / "store"))


def test_register_issues_id_and_token(db):
    reg = AgentRegistry(db)
    ident, token = reg.register("research-1", provider="anthropic", model="claude")
    assert ident.agent_id.startswith("agent-")
    assert token
    assert ident.provider == "anthropic"
    stored = db.get(ident.agent_id)
    assert stored.data["token_hash"] != token
    assert "token" not in stored.data


def test_authenticate_ok_and_rejects_wrong_token(db):
    reg = AgentRegistry(db)
    ident, token = reg.register("coder")
    got = reg.authenticate(ident.agent_id, token)
    assert got.agent_id == ident.agent_id
    with pytest.raises(PermissionError):
        reg.authenticate(ident.agent_id, "not-the-token")


def test_cannot_impersonate_missing_agent(db):
    reg = AgentRegistry(db)
    with pytest.raises(PermissionError):
        reg.authenticate("agent-does-not-exist", "x")
