"""Streamable HTTP handshake and transport semantics."""
from fastapi.testclient import TestClient

from embers import EmberDB
from embers.api import app, mcp_http
from embers.mcp.server import EmberMCP
from embers.mcp.tools import TOOLS


def test_streamable_http_discovery_and_notification(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_http, "_mcp", EmberMCP(db=EmberDB.connect(str(tmp_path / "store"))))
    client = TestClient(app)
    headers = {"Accept": "application/json, text/event-stream"}
    init = client.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "test", "version": "1"}}})
    assert init.status_code == 200
    assert init.headers["content-type"].startswith("application/json")
    assert init.json()["result"]["protocolVersion"] == "2025-06-18"
    assert init.json()["result"]["capabilities"] == {"tools": {}}
    assert "mcp-session-id" not in init.headers

    headers["MCP-Protocol-Version"] = "2025-06-18"
    initialized = client.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "method": "notifications/initialized"})
    assert initialized.status_code == 202
    assert initialized.content == b""

    listed = client.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert listed.status_code == 200
    assert {tool["name"] for tool in listed.json()["result"]["tools"]} == {
        tool["name"] for tool in TOOLS}
    assert len(listed.json()["result"]["tools"]) == 44
    assert client.get("/mcp", headers={"Accept": "text/event-stream"}).status_code == 405


def test_streamable_http_rejects_invalid_envelopes(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_http, "_mcp", EmberMCP(db=EmberDB.connect(str(tmp_path / "store"))))
    client = TestClient(app)
    message = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert client.post("/mcp", json=message,
                       headers={"MCP-Protocol-Version": "unknown"}).status_code == 400
    assert client.post("/mcp", json=[message]).status_code == 400
    assert client.post("/mcp", json=message,
                       headers={"Origin": "https://untrusted.example"}).status_code == 403
    assert client.post("/mcp", data="{", headers={"Content-Type": "application/json"}).status_code == 400
