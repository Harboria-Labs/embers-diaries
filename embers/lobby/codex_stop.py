"""Opt-in Codex Stop hook adapter for selected, redacted Lobby findings.

The hook reads the final assistant message only. It never opens a transcript or
tool log. A structured marker is the agent's explicit publication decision.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
from urllib.request import Request, urlopen

MARKER = re.compile(r"<ember-lobby-v2>(.*?)</ember-lobby-v2>", re.DOTALL)
MAX_ARTIFACT_BYTES = 16384


def _post(url, headers, payload):
    request = Request(url, data=json.dumps(payload).encode("utf-8"),
                      headers={**headers, "Content-Type": "application/json"},
                      method="POST")
    with urlopen(request, timeout=10) as response:
        return json.load(response)


def process_stop(event, settings, post=_post):
    """Return a publication receipt or None for a turn with no opted-in finding."""
    if event.get("hook_event_name") != "Stop" or event.get("stop_hook_active") is True:
        return None
    message = event.get("last_assistant_message") or ""
    markers = MARKER.findall(message)
    if not markers:
        return None
    if len(markers) != 1:
        raise ValueError("exactly one Lobby marker allowed")
    finding = json.loads(markers[0])
    if finding.get("share") is not True or finding.get("withhold") is True:
        return None
    turn_id = event.get("turn_id")
    if not isinstance(turn_id, str) or not turn_id or len(turn_id) > 110:
        raise ValueError("Codex turn_id required for idempotency")
    required = ("url", "agent_id", "token", "session_id", "scope_id")
    if any(not settings.get(k) for k in required):
        raise ValueError("Lobby adapter configuration incomplete")
    base = settings["url"].rstrip("/")
    if not base.startswith("http://127.0.0.1:") and not base.startswith("https://"):
        raise ValueError("Lobby URL must be loopback HTTP or HTTPS")
    route = base + "/v1/lobby/v2/scopes/" + settings["scope_id"]
    headers = {"X-Ember-Agent-Id": settings["agent_id"],
               "X-Ember-Token": settings["token"]}
    evidence = []
    artifact_name = finding.get("artifact_file")
    if artifact_name:
        if finding.get("redacted") is not True or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", artifact_name):
            raise ValueError("redacted outbox basename required")
        cwd = Path(event.get("cwd") or "").resolve()
        outbox = (cwd / ".ember-lobby-outbox").resolve()
        if outbox.parent != cwd:
            raise ValueError("Lobby outbox must be inside the working directory")
        artifact = (outbox / artifact_name).resolve()
        if artifact.parent != outbox or not artifact.is_file():
            raise ValueError("artifact must exist directly inside the Lobby outbox")
        content = artifact.read_text(encoding="utf-8")
        if not content or len(content.encode("utf-8")) > MAX_ARTIFACT_BYTES:
            raise ValueError("empty or oversized redacted artifact")
        uploaded = post(route + "/artifacts", headers, {
            "session_id": settings["session_id"], "content": content,
            "safe_to_share": True, "redacted": True,
            "client_artifact_id": "codex-stop:" + turn_id})
        evidence.append({"reference": uploaded["reference"],
                         "source": "codex-stop-redacted-artifact",
                         "observed_at": uploaded["observed_at"],
                         "execution_context": "Codex Stop hook; sha256=" + uploaded["sha256"]})
    payload = {"session_id": settings["session_id"], "share": True,
               "client_submission_id": "codex-stop:" + turn_id,
               "kind": finding["kind"], "claim": finding["claim"],
               "uncertainty": finding.get("uncertainty", "unverified"),
               "uncertainty_note": finding.get("uncertainty_note", ""),
               "evidence_status": "attached" if evidence else "unavailable",
               "evidence_refs": evidence}
    return post(route + "/items", headers, payload)


def main():
    try:
        event = json.load(sys.stdin)
        settings = {"url": os.environ.get("EMBER_LOBBY_URL"),
                    "agent_id": os.environ.get("EMBER_LOBBY_AGENT_ID"),
                    "token": os.environ.get("EMBER_LOBBY_TOKEN"),
                    "session_id": os.environ.get("EMBER_LOBBY_SESSION_ID"),
                    "scope_id": os.environ.get("EMBER_LOBBY_SCOPE_ID")}
        process_stop(event, settings)
    except Exception as exc:
        # A publication failure never changes the Codex turn result. The error
        # is local hook stderr; credentials and artifact bytes are not printed.
        print("Ember Lobby hook: " + type(exc).__name__, file=sys.stderr)
    print("{}")  # Stop hooks expect JSON output.


if __name__ == "__main__":
    main()
