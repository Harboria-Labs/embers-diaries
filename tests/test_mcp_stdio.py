"""
MCP stdio transport tests (spec §19) — driven through a REAL subprocess.

Why a subprocess: every existing MCP test calls `EmberMCP.handle()` directly,
which skips the transport entirely. Two defects lived happily behind that gap:

1. `EmberDB.connect()` printed its banner to **stdout** — the same channel
   JSON-RPC travels on — so a real client received two lines of prose before
   the first response and failed at handshake.
2. `_write()` emitted LSP-style `Content-Length` framing while MCP stdio is
   newline-delimited JSON. `_read()` accepts both, so the server round-tripped
   with itself perfectly and no in-process test could ever notice.

These tests read the child's actual stdout bytes, which is the only way to
prove the wire format is right.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TIMEOUT = 120


def _run(store: Path, stdin_text: str, env_extra: dict | None = None):
    """Run the stdio server as a real child process and capture both channels."""
    env = dict(os.environ)
    env["EMBER_STORE"] = str(store)
    env.update(env_extra or {})
    proc = subprocess.Popen(
        [sys.executable, "-m", "embers.mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", env=env,
    )
    out, err = proc.communicate(stdin_text, timeout=TIMEOUT)
    return out, err, proc.returncode


def _messages(stdout: str) -> list[dict]:
    """Parse the response stream as newline-delimited JSON.

    This is the assertion that matters: if ANYTHING non-JSON reaches stdout, or
    if a message is wrapped in Content-Length headers, this raises.
    """
    msgs = []
    for line in stdout.splitlines():
        if line.strip() == "":
            continue
        msgs.append(json.loads(line))
    return msgs


def _rpc(method: str, msg_id=None, **params) -> str:
    msg = {"jsonrpc": "2.0", "method": method}
    if msg_id is not None:
        msg["id"] = msg_id
    if params:
        msg["params"] = params
    return json.dumps(msg) + "\n"


class TestStdoutIsOnlyJSONRPC:
    """stdout is a protocol channel, not a log."""

    def test_stdout_carries_no_banner(self, tmp_path: Path):
        out, err, _ = _run(tmp_path / "s", _rpc("initialize", 1))
        assert "Ember" not in out, (
            "the connection banner reached the JSON-RPC channel; a real MCP "
            f"client cannot parse this: {out[:200]!r}")
        assert "Records:" not in out
        msgs = _messages(out)
        assert len(msgs) == 1
        assert msgs[0]["result"]["serverInfo"]["name"] == "ember-diaries"

    def test_banner_still_visible_on_stderr(self, tmp_path: Path):
        """The banner is not deleted — it moves to the channel meant for logs."""
        _, err, _ = _run(tmp_path / "s", _rpc("initialize", 1))
        assert "Ember" in err, "the banner should survive, on stderr"

    def test_index_rebuild_chatter_stays_off_stdout(self, tmp_path: Path):
        """A non-empty store takes the rebuild path, which also printed."""
        store = tmp_path / "s"
        from embers import EmberDB
        db = EmberDB.connect(str(store))
        from embers.core.record import EmberRecord
        db.write(EmberRecord(namespace="memories", data={"content": "seed"}))
        # drop the indexes so the child process must rebuild them on connect
        for meta in (store / "indexes").glob("*"):
            if meta.is_file():
                meta.unlink()

        out, _, _ = _run(store, _rpc("initialize", 1))
        assert "Rebuilding" not in out, f"rebuild log leaked to stdout: {out[:200]!r}"
        assert len(_messages(out)) == 1


class TestNewlineDelimitedFraming:
    """MCP stdio is newline-delimited JSON, one message per line."""

    def test_response_is_one_json_line_without_headers(self, tmp_path: Path):
        out, _, _ = _run(tmp_path / "s", _rpc("initialize", 1))
        assert "Content-Length" not in out, (
            "LSP-style framing is not the MCP stdio format")
        payload = [ln for ln in out.splitlines() if ln.strip()]
        assert len(payload) == 1, f"expected exactly one line, got {payload!r}"
        json.loads(payload[0])

    def test_each_request_gets_exactly_one_reply_in_order(self, tmp_path: Path):
        stdin = (_rpc("initialize", 1)
                 + _rpc("tools/list", 2)
                 + _rpc("ping", 3))
        out, _, _ = _run(tmp_path / "s", stdin)
        msgs = _messages(out)
        assert [m["id"] for m in msgs] == [1, 2, 3]
        assert "ember_write" in {t["name"] for t in msgs[1]["result"]["tools"]}

    def test_multiline_content_stays_on_one_line(self, tmp_path: Path):
        """A memory containing newlines must not split the response frame."""
        store = tmp_path / "s"
        stdin = (_rpc("initialize", 1)
                 + _rpc("tools/call", 2, name="ember_register",
                        arguments={"name": "luna"}))
        out, _, _ = _run(store, stdin)
        creds = json.loads(_messages(out)[1]["result"]["content"][0]["text"])

        stdin = (_rpc("tools/call", 3, name="ember_write", arguments={
            "content": "line one\nline two\nline three",
            "namespace": "memories",
            "agent_id": creds["agent_id"], "token": creds["token"],
        }) + _rpc("tools/call", 4, name="ember_search", arguments={
            "query": "line", "namespace": "memories",
            "agent_id": creds["agent_id"], "token": creds["token"],
        }))
        out, _, _ = _run(store, stdin)
        msgs = _messages(out)          # raises if the frame split on \n
        assert msgs[0]["result"]["isError"] is False
        hits = json.loads(msgs[1]["result"]["content"][0]["text"])
        assert any("line two" in json.dumps(h["data"]) for h in hits)


class TestTransportRobustness:
    def test_malformed_line_gets_parse_error_and_server_continues(self, tmp_path: Path):
        """A bad line must not desync or spin the read loop.

        The old loop did `except Exception: continue`, which silently dropped
        the line — and on a stream that stayed open, spun.
        """
        stdin = "this is not json\n" + _rpc("initialize", 7)
        out, _, code = _run(tmp_path / "s", stdin)
        msgs = _messages(out)
        assert len(msgs) == 2, f"expected parse error + reply, got {msgs!r}"
        assert msgs[0]["error"]["code"] == -32700
        assert msgs[0]["id"] is None
        assert msgs[1]["id"] == 7, "the next valid request must still be served"
        assert code == 0

    def test_notifications_get_no_reply(self, tmp_path: Path):
        """A JSON-RPC notification has no id, so it must produce no response —
        replying with id:null would be a protocol violation."""
        stdin = (_rpc("notifications/initialized")
                 + _rpc("notifications/cancelled", requestId=1)
                 + _rpc("ping", 5))
        out, _, _ = _run(tmp_path / "s", stdin)
        msgs = _messages(out)
        assert [m["id"] for m in msgs] == [5], f"unexpected replies: {msgs!r}"

    def test_unknown_method_with_id_is_method_not_found(self, tmp_path: Path):
        out, _, _ = _run(tmp_path / "s", _rpc("resources/list", 9))
        msgs = _messages(out)
        assert msgs[0]["error"]["code"] == -32601

    def test_eof_exits_cleanly(self, tmp_path: Path):
        out, _, code = _run(tmp_path / "s", "")
        assert code == 0
        assert _messages(out) == []

    def test_lsp_framed_input_is_still_accepted(self, tmp_path: Path):
        """`_read()` stays tolerant of Content-Length input for any client that
        speaks it — we changed what we WRITE, not what we accept."""
        body = json.dumps({"jsonrpc": "2.0", "id": 11, "method": "ping"})
        stdin = f"Content-Length: {len(body.encode())}\r\n\r\n{body}"
        out, _, _ = _run(tmp_path / "s", stdin)
        assert _messages(out)[0]["id"] == 11


def test_full_evidence_pipeline_over_the_real_transport(tmp_path: Path):
    """End-to-end over stdio: the exact flow that was reported broken.

    propose (with sealed evidence) → submit → evidence_for, with every byte
    crossing a real pipe.
    """
    store = tmp_path / "s"
    out, _, _ = _run(store, _rpc("tools/call", 1, name="ember_register",
                                 arguments={"name": "luna"}))
    creds = json.loads(_messages(out)[0]["result"]["content"][0]["text"])
    auth = {"agent_id": creds["agent_id"], "token": creds["token"]}

    out, _, _ = _run(store, _rpc("tools/call", 2, name="ember_propose_memory",
                                 arguments={
        "discovery": {"content": "stdio parser drops frames above 64KB"},
        "reason": "reproduced twice",
        "confidence": 0.95,
        "namespace": "memories",
        "evidence": [{"source": "run.log", "source_type": "directly_observed",
                      "description": "observed directly"}],
        **auth,
    }))
    pid = json.loads(_messages(out)[0]["result"]["content"][0]["text"])["proposal_id"]

    out, _, _ = _run(store, _rpc("tools/call", 3, name="ember_submit",
                                 arguments={"proposal_id": pid, **auth}))
    result = json.loads(_messages(out)[0]["result"]["content"][0]["text"])
    assert result["promoted"] is True, result
    memory_id = result["memory_id"]

    out, _, _ = _run(store, _rpc("tools/call", 4, name="ember_evidence_for",
                                 arguments={"memory_id": memory_id, **auth}))
    evidence = json.loads(_messages(out)[0]["result"]["content"][0]["text"])
    assert [e["source"] for e in evidence] == ["run.log"]
    assert evidence[0]["content_hash"], "evidence must arrive sealed"
