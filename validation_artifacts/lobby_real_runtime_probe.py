"""Opt-in isolated Codex Stop-hook probe. Never uses the configured live store."""

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time
from urllib.request import Request, urlopen

import uvicorn

import embers.api as api
from embers.db import EmberDB
from embers.lobby import working


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".lobby_real_runtime_probe_20261019"


def post(base, route, data, headers=None):
    request = Request(base + route, data=json.dumps(data).encode(), method="POST",
                      headers={"Content-Type": "application/json", **(headers or {})})
    with urlopen(request, timeout=10) as response:
        return json.load(response)


def get(base, route, headers):
    with urlopen(Request(base + route, headers=headers), timeout=10) as response:
        return json.load(response)


def main():
    WORK.mkdir(exist_ok=True)
    api._db = EmberDB.connect(str(WORK / "store"))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(uvicorn.Config(api.app, host="127.0.0.1", port=port,
                                           lifespan="off", log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(.1)
        if not server.started:
            raise RuntimeError("isolated API did not start")
        author = post(base, "/v1/agents/register", {"name": "real-codex-probe"})
        reader = post(base, "/v1/agents/register", {"name": "real-reader-probe"})
        ah = {"X-Ember-Agent-Id": author["agent_id"], "X-Ember-Token": author["token"]}
        rh = {"X-Ember-Agent-Id": reader["agent_id"], "X-Ember-Token": reader["token"]}
        task = "isolated-real-hook-probe"
        namespace = "isolated-lobby"
        a_sid = post(base, "/v1/sessions", {"task": task, "namespace": namespace}, ah)["session_id"]
        r_sid = post(base, "/v1/sessions", {"task": task, "namespace": namespace}, rh)["session_id"]
        scope = post(base, "/v1/lobby/v2/scopes", {"session_id": a_sid,
                     "task": task, "namespace": namespace}, ah)["scope_id"]
        post(base, f"/v1/lobby/v2/scopes/{scope}/members",
             {"session_id": a_sid, "agent_id": reader["agent_id"]}, ah)
        (WORK / "probe.py").write_text("assert 2 + 2 == 4\nprint('arithmetic probe passed')\n", encoding="utf-8")
        output = subprocess.run([str(ROOT / "diaries/Scripts/python.exe"), "probe.py"],
                                cwd=WORK, capture_output=True, text=True, check=True).stdout
        outbox = WORK / ".ember-lobby-outbox"
        outbox.mkdir(exist_ok=True)
        (outbox / "result.txt").write_text(output, encoding="utf-8")
        hooks = WORK / ".codex"
        hooks.mkdir(exist_ok=True)
        command = 'python -m embers.lobby.codex_stop'
        (hooks / "hooks.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": command, "timeout": 30}]}]}}), encoding="utf-8")
        env = os.environ.copy()
        env.update({"CODEX_HOME": str(Path.home() / ".codex"), "PYTHONPATH": str(ROOT),
                    "EMBER_LOBBY_URL": base, "EMBER_LOBBY_AGENT_ID": author["agent_id"],
                    "EMBER_LOBBY_TOKEN": author["token"], "EMBER_LOBBY_SESSION_ID": a_sid,
                    "EMBER_LOBBY_SCOPE_ID": scope})
        manual = subprocess.run([str(ROOT / "diaries/Scripts/python.exe"),
                                  "-m", "embers.lobby.codex_stop"], cwd=WORK, env=env,
                                input=json.dumps({"hook_event_name": "Stop", "turn_id": "manual-check",
                                                  "cwd": str(WORK),
                                                  "last_assistant_message": "no publication marker"}),
                                capture_output=True, text=True, timeout=15)
        prompt = ("The integrated harness just ran probe.py and captured the exact output "
                  "'arithmetic probe passed' in the redacted outbox result.txt. "
                  "No tool call is needed or permitted in this isolated runtime. "
                  "Report exactly one completed finding "
                  "in your final answer using this literal marker: "
                  "<ember-lobby-v2>{\"share\":true,\"kind\":\"finding\","
                  "\"claim\":\"The isolated arithmetic probe passed\","
                  "\"uncertainty\":\"observed\",\"artifact_file\":\"result.txt\","
                  "\"redacted\":true}</ember-lobby-v2>. "
                  "The outbox file contains only the permitted test output. Do not publish anything else.")
        hook_config = 'hooks.Stop=[{hooks=[{type="command",command=' + json.dumps(command) + ',timeout=30}]}]'
        command = [shutil.which("codex.cmd") or "codex.cmd", "--dangerously-bypass-hook-trust",
                   "-c", hook_config,
                   "-C", str(WORK), "-a", "never", "exec",
                   "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
                   "-s", "workspace-write", prompt]
        run = subprocess.run(command, cwd=WORK, env=env, capture_output=True, text=True, timeout=150)
        items = get(base, f"/v1/lobby/v2/scopes/{scope}/items?session_id={r_sid}&context=arithmetic", rh)["items"]
        first_ok = any(i["claim"] == "The isolated arithmetic probe passed" for i in items)
        if not first_ok:
            raise RuntimeError("real Codex hook did not publish a finding")
        cursor = working.events(api._db, scope, a_sid, author["agent_id"])["cursor"]
        stream_url = f"{base}/v1/lobby/v2/scopes/{scope}/stream?session_id={a_sid}&after={cursor}"
        second_marker = ('<ember-lobby-v2>{"share":true,"kind":"outcome",'
                         '"claim":"Second agent retrieved the first finding",'
                         '"uncertainty":"unverified"}</ember-lobby-v2>')
        second_prompt = ("Your authorized harness retrieved this provisional Ember memory: "
                         + items[0]["claim"] + ". Report the completed retrieval as a provisional "
                         "outcome, with no fabricated evidence, using exactly this marker: "
                         + second_marker + ". Do not call tools or publish anything else.")
        second_env = {**env, "EMBER_LOBBY_AGENT_ID": reader["agent_id"],
                      "EMBER_LOBBY_TOKEN": reader["token"], "EMBER_LOBBY_SESSION_ID": r_sid}
        second_command = [*command[:-1], second_prompt]
        with urlopen(Request(stream_url, headers=ah), timeout=10) as feed:
            while feed.readline().strip() != b"":
                pass  # ready frame
            second = subprocess.run(second_command, cwd=WORK, env=second_env,
                                    capture_output=True, text=True, timeout=150)
            frame = []
            while True:
                line = feed.readline().decode().strip()
                if not line:
                    if frame:
                        break
                    continue
                frame.append(line)
        reflected = get(base, f"/v1/lobby/v2/scopes/{scope}/items?session_id={a_sid}&context=retrieved", ah)["items"]
        second_ok = any(i["claim"] == "Second agent retrieved the first finding" for i in reflected)
        result = {"manual_hook_exit": manual.returncode,
                  "manual_hook_valid_output": manual.stdout.strip() == "{}",
                  "codex_exit": run.returncode, "first_hook_completed": "hook: Stop Completed" in run.stderr,
                  "marker_in_real_final": "<ember-lobby-v2>" in run.stdout,
                  "reader_retrieved": first_ok,
                  "attached_evidence_count": len(items[0]["evidence_refs"]) if items else 0,
                  "second_codex_exit": second.returncode,
                  "second_hook_completed": "hook: Stop Completed" in second.stderr,
                  "second_item_retrieved_by_first": second_ok,
                  "sse_changed": "event: working_memory_changed" in frame,
                  "sse_cursor": next((line[4:] for line in frame if line.startswith("id: ")), None)}
        print(json.dumps(result))
        if not all((result["first_hook_completed"], result["second_hook_completed"],
                    second_ok, result["sse_changed"])):
            raise RuntimeError("two-runtime Lobby workflow incomplete")
    finally:
        server.should_exit = True
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
