"""Post-scale evidence tasks. Run after (or beside, lightly) the scale write.

    python -m benchmark.tasks.post_run --run-dir benchmark/results/run-YYYYMMDD-HHMMSS

Never writes to `memories`. Never prints tokens or observation codes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from benchmark.adapters.ember_mcp import EmberMcpAdapter
from benchmark.adapters.ember_rest import EmberRestAdapter
from benchmark.run import (
    GROK_AGENT, PROTECTED_NAMESPACES, health_probe, recorder_cmd,
    score_queries, snapshot_passive, write_body,
)
from benchmark.util import (
    JsonlLog, dump_json, load_json, load_jsonl, now_iso, percentiles,
    ranked_ids, recall_at, reciprocal_rank, wat_label,
)


def load_auth(secrets: Path, adapter: EmberRestAdapter) -> dict:
    data = json.loads((secrets / "agent.json").read_text())
    adapter.set_auth(data["agent_id"], data["token"], data.get("session_id"))
    return data


def rescore_exact(adapter, run_dir: Path, namespace: str, log: JsonlLog) -> dict:
    """Re-score exact_token queries using the fixed dataset (tokens match content)."""
    meta = load_json(run_dir / "meta.json")
    scale = meta["scale"]
    uid_to_id = load_json(run_dir / "uid_to_id.json")
    queries = [q for q in load_jsonl(ROOT / "benchmark/dataset/queries.jsonl")
               if q["type"] == "exact_token"]
    out = {}
    for cp in meta["checkpoints"]:
        qv = [q for q in queries if q["min_index"] < cp]
        scores = score_queries(adapter, qv, uid_to_id, namespace, log, tag=f"exact-fixed-{cp}")
        out[str(cp)] = scores
        ov = scores.get("overall") or {}
        print(f"exact_token FIXED scale={cp} n={ov.get('n')} R@1={ov.get('recall@1')} "
              f"R@5={ov.get('recall@5')} R@10={ov.get('recall@10')} MRR={ov.get('mrr')}", flush=True)
    dump_json(run_dir / "rescore-exact-token-fixed.json", out)
    return out


def try_supersede_via_mcp(mcp: EmberMcpAdapter, rest: EmberRestAdapter,
                          run_dir: Path, namespace: str, log: JsonlLog) -> dict:
    """Use MCP ember_read (has content_hash) then REST/MCP update. Bench ns only."""
    mems = load_jsonl(ROOT / "benchmark/dataset/memories.jsonl")
    uid_to_id = load_json(run_dir / "uid_to_id.json")
    targets = [m for m in mems if m["kind"] == "supersede_v1" and m["uid"] in uid_to_id]
    applied, failed = 0, []
    for m in targets:
        rid = uid_to_id[m["uid"]]
        # skip if already a new id stored
        if m["uid"] + "#old" in uid_to_id:
            applied += 1
            continue
        rd = mcp.read(rid)
        log.write({"op": "mcp_read", "uid": m["uid"], "status": rd.get("status"),
                   "ok": rd.get("ok"), "latency_ms": rd.get("latency_ms")})
        parsed = rd.get("parsed") or {}
        expected = parsed.get("content_hash") if isinstance(parsed, dict) else None
        if not expected:
            failed.append({"uid": m["uid"], "reason": "mcp_no_hash", "ok": rd.get("ok")})
            continue
        upd = mcp.call_tool("ember_update", {
            "record_id": rid,
            "data": {"content": m["update_to"], "subject": m["subject"]},
            "expected_hash": expected,
            "creation_reason": f"benchmark-supersede:{m['uid']}",
        })
        log.write({"op": "mcp_update", "uid": m["uid"], "ok": upd.get("ok"),
                   "status": upd.get("status"), "latency_ms": upd.get("latency_ms")})
        new_id = None
        parsed_u = upd.get("parsed") or {}
        if isinstance(parsed_u, dict):
            new_id = parsed_u.get("record_id") or parsed_u.get("id")
        if upd.get("ok") and new_id:
            uid_to_id[m["uid"] + "#old"] = rid
            uid_to_id[m["uid"]] = new_id
            applied += 1
        else:
            failed.append({"uid": m["uid"], "reason": "update_failed", "ok": upd.get("ok")})
    dump_json(run_dir / "uid_to_id.json", uid_to_id)
    # Re-score supersession queries (current id should win)
    queries = [q for q in load_jsonl(ROOT / "benchmark/dataset/queries.jsonl") if q["type"] == "supersession"]
    scores = score_queries(rest, queries, uid_to_id, namespace, log, tag="supersession-after-update")
    # Also ask history on a sample
    histories = []
    for m in targets[:5]:
        rid = uid_to_id.get(m["uid"])
        old = uid_to_id.get(m["uid"] + "#old")
        if not rid:
            continue
        h = rest.history(rid)
        body = h.get("body") or {}
        hist = body.get("history") if isinstance(body, dict) else None
        ids = [x.get("id") for x in (hist or []) if isinstance(x, dict)]
        histories.append({
            "uid": m["uid"], "current": rid, "old": old, "ok": h.get("ok"),
            "history_len": len(hist or []), "history_ids_count": len(ids),
            "old_in_history": old in ids if old else None,
            "latency_ms": h.get("latency_ms"),
        })
        log.write({"op": "history", "uid": m["uid"], "ok": h.get("ok"), "latency_ms": h.get("latency_ms"),
                   "history_len": len(hist or [])})
    result = {"targets": len(targets), "applied": applied, "failed": len(failed),
              "failures_sample": failed[:6], "recall_after": scores, "history_sample": histories}
    dump_json(run_dir / "supersession-mcp.json", result)
    print(f"supersession MCP applied={applied}/{len(targets)} failed={len(failed)} "
          f"recall={ (scores.get('overall') or {}) }", flush=True)
    return result


def features(rest: EmberRestAdapter, mcp: EmberMcpAdapter, run_dir: Path,
             ns_feat: str, ns_scale: str, log: JsonlLog) -> dict:
    """Exercise remaining Ember surfaces on a dedicated bench-* namespace."""
    assert ns_feat.startswith("bench-") and ns_feat != "memories"
    recorder_cmd("screenshot", "features-start")
    out: dict = {"namespace": ns_feat, "tests": []}

    def rec(name, result, **extra):
        row = {"name": name, "ok": result.get("ok"), "status": result.get("status"),
               "latency_ms": result.get("latency_ms"), **extra}
        # do not store full bodies if they might contain codes/tokens
        body = result.get("body") or result.get("parsed")
        if isinstance(body, dict):
            safe = {k: body[k] for k in body if not re.search(r"token|code|secret", k, re.I)}
            # shrink
            row["body_keys"] = list(body.keys())
            for k in ("id", "record_id", "conflict_id", "proposal_id", "feedback_id",
                      "session_id", "scope_id", "item_id", "count", "failure_id", "run_id"):
                if k in body:
                    row[k] = body[k]
        log.write({"op": "feature", **row})
        out["tests"].append(row)
        print(f"  feature {name} ok={row['ok']} status={row['status']} lat={row.get('latency_ms')}", flush=True)
        return row

    # two conflicting facts
    a = rest.write({"content": "The bench clock in Hall A is set to 09:00. Token TOKBENCHA.",
                    "subject": "Hall A clock", "namespace": ns_feat, "tags": ["conflict", "clock"],
                    "memory_type": "raw", "room": "task", "creation_reason": "benchmark:conflict-a"})
    b = rest.write({"content": "The bench clock in Hall A is set to 17:00. Token TOKBENCHB.",
                    "subject": "Hall A clock", "namespace": ns_feat, "tags": ["conflict", "clock"],
                    "memory_type": "raw", "room": "task", "creation_reason": "benchmark:conflict-b"})
    rec("write_conflict_a", a, ids=a.get("ids"))
    rec("write_conflict_b", b, ids=b.get("ids"))
    ida = (a.get("ids") or [None])[0]
    idb = (b.get("ids") or [None])[0]
    if ida and idb:
        mapped = rest.raw("POST", "/v1/conflicts/map", json_body={
            "memory_a": ida, "memory_b": idb, "conflict_type": "semantic",
            "note": "benchmark same-subject clock conflict",
        })
        rec("conflict_map", mapped)
        opened = rest.raw("GET", "/v1/conflicts/open", params={"namespace": ns_feat})
        rec("conflicts_open", opened,
            count=(opened.get("body") or {}).get("count") if isinstance(opened.get("body"), dict) else None)
        cid = None
        body = mapped.get("body") or {}
        if isinstance(body, dict):
            cid = body.get("conflict_id") or (body.get("conflict") or {}).get("id") or body.get("id")
        if cid:
            # Do not "resolve" in a way that might be confused — it's our bench record only.
            resolved = rest.raw("POST", f"/v1/conflicts/{cid}/resolve", json_body={
                "status": "resolved", "resolution": "benchmark: leave both; recorded for the harness",
                "winner_id": ida,
            })
            rec("conflict_resolve", resolved)

    # orient
    ori = rest.orient("Hall A clock time", namespace=ns_feat)
    rec("orient", ori)

    # search + query (bench ns only)
    sea = rest.search("Hall A clock", namespace=ns_feat, top_k=5)
    rec("search", sea)
    qry = rest.query(namespace=ns_feat, limit=10)
    rec("query", qry, count=(qry.get("body") or {}).get("count") if isinstance(qry.get("body"), dict) else None)

    # feedback on our record, then recall again to see if anything observable changes
    if ida:
        r1 = rest.recall("Hall A clock", namespace=ns_feat, top_k=5)
        ids_before = ranked_ids(r1.get("body"))
        rec("recall_before_feedback", r1, ranked_head=ids_before[:5])
        fb = rest.feedback(ida, "useful", signal=1, channel="explicit")
        rec("feedback_useful", fb)
        listed = rest.list_feedback(ida)
        rec("list_feedback", listed)
        r2 = rest.recall("Hall A clock", namespace=ns_feat, top_k=5)
        ids_after = ranked_ids(r2.get("body"))
        rec("recall_after_feedback", r2, ranked_head=ids_after[:5],
            order_changed=ids_before != ids_after, same_top1=(ids_before[:1] == ids_after[:1]))
        # irrelevant on the other
        if idb:
            fb2 = rest.feedback(idb, "irrelevant", signal=-1, channel="explicit")
            rec("feedback_irrelevant", fb2)
        life = rest.lifecycle(ida)
        rec("lifecycle", life)
        graph = rest.graph(ida, depth=1)
        rec("graph", graph)

    # lobby v1
    sess = rest.start_session("benchmark-lobby", ns_feat)
    rec("session_lobby", sess)
    sid = (sess.get("body") or {}).get("session_id") if isinstance(sess.get("body"), dict) else None
    if sid:
        rest.session_id = sid
        pub = rest.raw("POST", "/v1/lobby/publish", json_body={
            "session_id": sid, "task": "benchmark-lobby", "namespace": ns_feat,
            "room": "task", "type": "discovery", "body": "bench lobby publish",
        })
        rec("lobby_publish", pub)
        st = rest.raw("GET", "/v1/lobby/status", headers={"X-Ember-Session-Id": sid})
        rec("lobby_status", st)
        # v2 scope
        scope = rest.raw("POST", "/v1/lobby/v2/scopes", json_body={
            "session_id": sid, "task": "benchmark-lobby", "namespace": ns_feat,
        })
        rec("lobby_v2_scope", scope)
        scope_id = (scope.get("body") or {}).get("scope_id") if isinstance(scope.get("body"), dict) else None
        if scope_id:
            item = rest.raw("POST", f"/v1/lobby/v2/scopes/{scope_id}/items", json_body={
                "session_id": sid,
                "title": "bench item",
                "body": "a benchmark lobby item",
                "type": "note",
            })
            rec("lobby_v2_submit", item)
            items = rest.raw("GET", f"/v1/lobby/v2/scopes/{scope_id}/items", params={"session_id": sid})
            rec("lobby_v2_list", items)

    # proposals — try and record whatever happens (evidence rules may reject)
    prop = rest.raw("POST", "/v1/memory/propose", json_body={
        "content": "A proposed bench fact about Hall A windows.",
        "namespace": ns_feat, "session_id": sid,
    })
    rec("propose", prop)
    plist = rest.raw("GET", "/v1/memory/proposals", params={"namespace": ns_feat})
    rec("list_proposals", plist)

    # failures
    fail = rest.raw("POST", "/v1/failures", json_body={
        "namespace": ns_feat, "approach": "benchmark-probe",
        "failed": "intentional bench failure report", "cause": "harness",
        "session_id": sid,
    })
    rec("report_failure", fail)
    flist = rest.raw("GET", "/v1/failures", params={"approach": "benchmark-probe"})
    rec("list_failures", flist)

    # epistemic state (read) on our id
    if ida:
        ep = rest.raw("GET", f"/v1/epistemic/state/{ns_feat}")
        rec("epistemic_state", ep)

    # pair relationship if endpoint accepts
    if ida and idb:
        pair = rest.raw("POST", "/v1/memory/pair-relationship", json_body={
            "a": ida, "b": idb, "namespace": ns_feat,
        })
        rec("pair_relationship", pair)

    # observer/visualizer access via MCP for OUR bench ns (codes not logged)
    vis = mcp.visualize(span_sessions=True, ttl_seconds=3600, namespaces=[ns_feat, ns_scale])
    rec("mcp_ember_visualize", vis, has_code=bool((vis.get("parsed") or {}).get("code")) if isinstance(vis.get("parsed"), dict) else None,
        has_path=bool((vis.get("parsed") or {}).get("viewer_path")) if isinstance(vis.get("parsed"), dict) else None)
    vns = mcp.visualizer_access(ns_feat, ttl_seconds=3600)
    rec("mcp_ember_visualizer_access", vns,
        has_path=bool((vns.get("parsed") or {}).get("viewer_path")) if isinstance(vns.get("parsed"), dict) else None)
    # REST equivalents
    rvis = rest.raw("POST", "/v1/observer-access", json_body={
        "span_sessions": True, "ttl_seconds": 3600, "namespaces": [ns_feat],
    })
    rec("rest_observer_access", rvis,
        has_path="viewer_path" in (rvis.get("body") or {}) if isinstance(rvis.get("body"), dict) else False)
    rva = rest.raw("POST", "/v1/visualizer-access", json_body={"namespace": ns_feat, "ttl_seconds": 3600})
    rec("rest_visualizer_access", rva,
        has_path="viewer_path" in (rva.get("body") or {}) if isinstance(rva.get("body"), dict) else False)

    # integrity audit — bench namespaces ONLY (read-only, once)
    audit = rest.raw("POST", "/v1/maintenance/integrity/audits", json_body={"namespaces": [ns_feat, ns_scale]})
    rec("integrity_audit", audit)
    run_id = (audit.get("body") or {}).get("run_id") if isinstance(audit.get("body"), dict) else None
    if run_id:
        st = rest.raw("GET", f"/v1/maintenance/integrity/audits/{run_id}")
        rec("integrity_audit_status", st)

    # GET session
    if sid:
        gs = rest.raw("GET", f"/v1/sessions/{sid}")
        rec("get_session", gs)

    recorder_cmd("screenshot", "features-done")
    dump_json(run_dir / "features.json", out)
    return out


def errors_and_limits(rest: EmberRestAdapter, run_dir: Path, ns: str, log: JsonlLog) -> dict:
    assert ns.startswith("bench-")
    tests = []

    def rec(name, result, **extra):
        row = {"name": name, "ok": result.get("ok"), "status": result.get("status"),
               "latency_ms": result.get("latency_ms"), "error": result.get("error"),
               **extra}
        body = result.get("body")
        if isinstance(body, dict):
            row["detail"] = body.get("detail")
            row["body_keys"] = list(body.keys())
        log.write({"op": "error_case", **row})
        tests.append(row)
        print(f"  error {name} status={row['status']} ok={row['ok']}", flush=True)
        return row

    rec("empty_query", rest.recall("", namespace=ns, top_k=5))
    rec("missing_content", rest.raw("POST", "/v1/memory/write", json_body={"namespace": ns, "subject": "x"}))
    rec("malformed_json", rest.raw("POST", "/v1/memory/write", raw_body=b"{not json", content_type="application/json"))
    rec("batch_101", rest.write_batch(
        [{"content": f"overflow {i}", "namespace": ns, "memory_type": "raw"} for i in range(101)]
    ))
    rec("nonexistent_read", rest.read("does-not-exist-zzzz"))
    rec("nonexistent_feedback", rest.feedback("does-not-exist-zzzz", "useful", signal=1, channel="explicit"))
    unicode_w = rest.write({"content": "Unicode bench: 日本語 العربية Зоë — TOKUNI01.",
                            "subject": "unicode bench", "namespace": ns, "memory_type": "raw",
                            "creation_reason": "benchmark:unicode"})
    rec("unicode_write", unicode_w, ids=unicode_w.get("ids"))
    if unicode_w.get("ids"):
        rec("unicode_recall", rest.recall("TOKUNI01", namespace=ns, top_k=5),
            ids=ranked_ids((rest.recall("日本語", namespace=ns, top_k=5)).get("body")))
    # very long content (~200k)
    long_c = "LONG " * 40000  # ~200k chars
    rec("very_long_content", rest.write({"content": long_c, "subject": "long", "namespace": ns,
                                         "memory_type": "raw", "creation_reason": "benchmark:long"}))
    # oversized payload ~2MB
    huge = ("X" * 2_000_000).encode()
    rec("oversized_2mb", rest.raw("POST", "/v1/memory/write", raw_body=b'{"content":"' + huge + b'","namespace":"' + ns.encode() + b'"}',
                                  content_type="application/json", timeout=30))
    dump_json(run_dir / "errors.json", {"tests": tests})
    return {"tests": tests}


def auth_cases(rest: EmberRestAdapter, run_dir: Path, ns: str, log: JsonlLog) -> dict:
    tests = []

    def rec(name, result, **extra):
        row = {"name": name, "ok": result.get("ok"), "status": result.get("status"),
               "latency_ms": result.get("latency_ms"), **extra}
        if isinstance(result.get("body"), dict):
            row["detail"] = result["body"].get("detail")
        log.write({"op": "auth_case", **row})
        tests.append(row)
        print(f"  auth {name} status={row['status']}", flush=True)
        return row

    rec("no_auth_write", rest.raw("POST", "/v1/memory/write",
                                  json_body={"content": "x", "namespace": ns}, auth=False))
    rec("bad_token", rest.raw("POST", "/v1/memory/write",
                              json_body={"content": "x", "namespace": ns},
                              headers={"X-Ember-Agent-Id": rest.agent_id or "agent-x",
                                       "X-Ember-Token": "definitely-not-a-real-token"}))
    # second agent
    other = EmberRestAdapter(rest.base_url)
    reg = other.register("bench-ember-intruder-2026-11-13", "benchmark-harness", "ember-bench-intruder")
    rec("register_second_agent", reg)
    if reg.get("ok"):
        oid, otok = reg["body"]["agent_id"], reg["body"]["token"]
        # store privately
        secrets = Path(__file__).resolve().parent.parent / ".secrets"
        (secrets / "intruder.json").write_text(json.dumps({"agent_id": oid, "note": "token stored separately"}))
        # do not write token to run dir
        other.set_auth(oid, otok)
        # unregistered namespace is open by default — test that honestly
        read_open = other.raw("POST", "/v1/memory/recall",
                              json_body={"query": "Hall A", "namespace": ns, "top_k": 3})
        rec("second_agent_recall_unregistered_ns", read_open,
            note="Unregistered namespaces are open according to Ember ACL; this is a measured result.")
        write_open = other.write({"content": "intruder probe — should note ACL", "namespace": ns,
                                  "memory_type": "raw", "creation_reason": "benchmark:acl-probe"})
        rec("second_agent_write_unregistered_ns", write_open)
        # cannot (and must not) try Grok's memories for denial — skip private-ns test if no API to create one
        rec("private_ns_denial", {"ok": False, "status": None, "latency_ms": 0,
                                  "body": {"detail": "skipped: no REST API to create a PRIVATE namespace; "
                                           "not testing against Grok's memories namespace"}},
            skipped=True)
        other.close()
    # wrong agent id with our token
    rec("wrong_agent_id", rest.raw("POST", "/v1/memory/recall",
                                   json_body={"query": "Hall A", "namespace": ns, "top_k": 3},
                                   headers={"X-Ember-Agent-Id": "agent-00000000-0000-0000-0000-000000000000",
                                            "X-Ember-Token": rest._token or "x"}))
    dump_json(run_dir / "auth.json", {"tests": tests})
    return {"tests": tests}


def mcp_compare(rest: EmberRestAdapter, mcp: EmberMcpAdapter, run_dir: Path,
                ns: str, log: JsonlLog, n: int = 20) -> dict:
    """Same 20 writes + 20 recalls on MCP vs REST, latency only. Bench ns."""
    rest_w, mcp_w, rest_r, mcp_r = [], [], [], []
    ids = []
    for i in range(n):
        body = {"content": f"MCP/REST compare item {i} token TOKCMP{i:03d}.",
                "subject": f"compare-{i}", "namespace": ns, "memory_type": "raw",
                "room": "task", "creation_reason": f"benchmark:mcp-compare:{i}"}
        rw = rest.write(body)
        rest_w.append(rw.get("latency_ms"))
        log.write({"op": "compare_rest_write", "i": i, "latency_ms": rw.get("latency_ms"), "ok": rw.get("ok")})
        mw = mcp.write(body)
        mcp_w.append(mw.get("latency_ms"))
        log.write({"op": "compare_mcp_write", "i": i, "latency_ms": mw.get("latency_ms"), "ok": mw.get("ok")})
        if rw.get("ids"):
            ids.append((f"TOKCMP{i:03d}", rw["ids"][0]))
    for tok, _ in ids:
        rr = rest.recall(tok, namespace=ns, top_k=5)
        rest_r.append(rr.get("latency_ms"))
        log.write({"op": "compare_rest_recall", "latency_ms": rr.get("latency_ms"), "ok": rr.get("ok")})
        mr = mcp.recall(tok, namespace=ns, top_k=5)
        mcp_r.append(mr.get("latency_ms"))
        log.write({"op": "compare_mcp_recall", "latency_ms": mr.get("latency_ms"), "ok": mr.get("ok")})
    tools = mcp.tools_list()
    tool_names = []
    if isinstance(tools.get("body"), dict):
        result = tools["body"].get("result") or {}
        tool_names = [t.get("name") for t in result.get("tools") or [] if isinstance(t, dict)]
    out = {
        "n": n,
        "rest_write": percentiles(rest_w),
        "mcp_write": percentiles(mcp_w),
        "rest_recall": percentiles(rest_r),
        "mcp_recall": percentiles(mcp_r),
        "mcp_tools_list_latency_ms": tools.get("latency_ms"),
        "mcp_tools_count": len(tool_names),
        "mcp_tool_names": tool_names,
        "mcp_initialize_already_done": True,
    }
    dump_json(run_dir / "mcp_compare.json", out)
    print(f"MCP vs REST write rest={out['rest_write']} mcp={out['mcp_write']}", flush=True)
    print(f"MCP vs REST recall rest={out['rest_recall']} mcp={out['mcp_recall']} tools={len(tool_names)}", flush=True)
    return out


def agent_loop(rest: EmberRestAdapter, run_dir: Path, ns: str, log: JsonlLog) -> dict:
    """Simulated Grok-style workflow: orient → recall → write → feedback.

    This is NOT Grok. Labeled as a simulated agent loop.
    """
    steps = []
    clues = "What is recorded about the Hall A clock?"
    t0 = time.monotonic()
    ori = rest.orient(clues, namespace=ns)
    steps.append({"step": "orient", "ok": ori.get("ok"), "latency_ms": ori.get("latency_ms")})
    rec = rest.recall(clues, namespace=ns, top_k=5)
    steps.append({"step": "recall", "ok": rec.get("ok"), "latency_ms": rec.get("latency_ms"),
                  "n": len(ranked_ids(rec.get("body")))})
    wr = rest.write({"content": "Simulated agent note: Hall A clock was reviewed in the bench loop.",
                     "subject": "simulated-agent-note", "namespace": ns, "memory_type": "episodic",
                     "room": "task", "creation_reason": "benchmark:simulated-agent-loop"})
    steps.append({"step": "write", "ok": wr.get("ok"), "latency_ms": wr.get("latency_ms"), "ids": wr.get("ids")})
    fb_ok = None
    if wr.get("ids"):
        fb = rest.feedback(wr["ids"][0], "useful", signal=1, channel="explicit",
                           retrieval_context=clues)
        steps.append({"step": "feedback", "ok": fb.get("ok"), "latency_ms": fb.get("latency_ms")})
        fb_ok = fb.get("ok")
    wall = (time.monotonic() - t0) * 1000
    out = {
        "label": "simulated Grok-style workflow, not Grok itself",
        "steps": steps,
        "wall_ms": wall,
        "all_ok": all(s.get("ok") for s in steps),
        "what_would_be_needed_to_benchmark_grok_directly": (
            "Grok's own session traces or consented tool-call logs, plus a durable "
            "MCP access-log that attributes calls to agent-bd98fca7-0c95-45df-a55f-063e41bc6831 "
            "with per-request latency. This run used passive WAL + uvicorn logs only, "
            "and did not open Grok's Observer with a code until Samuel issued one."
        ),
    }
    for s in steps:
        log.write({"op": "agent_loop", **s})
    dump_json(run_dir / "agent_loop.json", out)
    print(f"agent_loop wall={wall:.0f}ms all_ok={out['all_ok']} steps={[s['step'] for s in steps]}", flush=True)
    return out


def tunnel_sample(run_dir: Path, log: JsonlLog, n: int = 8) -> dict:
    """Small separately-reported sample against the public ngrok URL. Not mixed into localhost numbers."""
    url = "https://igloo-flaring-subpar.ngrok-free.dev"
    adapter = EmberRestAdapter(url, timeout=15.0, headers={"ngrok-skip-browser-warning": "true"})
    samples = []
    for i in range(n):
        r = adapter.health()
        samples.append({"i": i, "ok": r.get("ok"), "status": r.get("status"),
                        "latency_ms": r.get("latency_ms"), "error": r.get("error")})
        log.write({"op": "tunnel_health", **samples[-1]})
        time.sleep(0.2)
    adapter.close()
    ok = [s["latency_ms"] for s in samples if s.get("ok") and s.get("latency_ms") is not None]
    out = {"url_host": "igloo-flaring-subpar.ngrok-free.dev", "n": n,
           "ok": sum(1 for s in samples if s.get("ok")),
           "latency": percentiles(ok),
           "statuses": [s.get("status") for s in samples],
           "note": "Tunnel blips ~1 min at a time. These numbers are reported separately and are not mixed into localhost results."}
    dump_json(run_dir / "tunnel_sample.json", out)
    print(f"tunnel sample ok={out['ok']}/{n} lat={out['latency']}", flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--base-url", default="http://localhost:9200")
    args = ap.parse_args()
    run_dir = Path(args.run_dir)
    meta = load_json(run_dir / "meta.json")
    ns_scale = meta["namespaces"]["scale"]
    ns_feat = meta["namespaces"]["features"]
    assert ns_scale.startswith("bench-") and ns_feat.startswith("bench-")
    assert "memories" not in (ns_scale, ns_feat)

    rest = EmberRestAdapter(args.base_url)
    secrets = Path(__file__).resolve().parent.parent / ".secrets"
    auth = load_auth(secrets, rest)
    # restore session if we can
    sess_meta = None
    # start a post-run session in features ns
    sess = rest.start_session("benchmark-post-run", ns_feat)
    if sess.get("ok"):
        print(f"post-run session={(sess.get('body') or {}).get('session_id')}", flush=True)

    mcp = EmberMcpAdapter(args.base_url.rstrip("/") + "/mcp")
    mcp.set_auth(auth["agent_id"], auth["token"], rest.session_id)
    init = mcp.initialize()
    print(f"MCP initialize ok={init.get('ok')} status={init.get('status')} lat={init.get('latency_ms')}", flush=True)
    if rest.session_id:
        mcp.session_id = rest.session_id

    log = JsonlLog(run_dir / "raw-post.jsonl")
    recorder_cmd("screenshot", "post-run-start")
    snapshot_passive("post-run-start", run_dir / "grok" / "snapshot-post-run-start.json")

    print("=== rescore exact_token (fixed queries) ===", flush=True)
    exact = rescore_exact(rest, run_dir, ns_scale, log)

    print("=== supersession via MCP hash ===", flush=True)
    sup = try_supersede_via_mcp(mcp, rest, run_dir, ns_scale, log)

    print("=== features ===", flush=True)
    feat = features(rest, mcp, run_dir, ns_feat, ns_scale, log)

    print("=== errors and limits ===", flush=True)
    err = errors_and_limits(rest, run_dir, ns_feat, log)

    print("=== auth ===", flush=True)
    au = auth_cases(rest, run_dir, ns_feat, log)

    print("=== MCP vs REST ===", flush=True)
    cmp_ = mcp_compare(rest, mcp, run_dir, ns_feat, log, n=20)

    print("=== simulated agent loop ===", flush=True)
    loop = agent_loop(rest, run_dir, ns_feat, log)

    print("=== tunnel sample (separate) ===", flush=True)
    tun = tunnel_sample(run_dir, log, n=8)

    snapshot_passive("post-run-end", run_dir / "grok" / "snapshot-post-run-end.json")
    h = health_probe(rest)
    dump_json(run_dir / "health-post.json", h)
    recorder_cmd("screenshot", "post-run-done")
    log.close()
    rest.close()
    mcp.close()
    print("POST-RUN DONE", flush=True)


if __name__ == "__main__":
    main()
