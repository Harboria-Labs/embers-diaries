"""Ember live benchmark runner.

    python -m benchmark.run --target ember-rest --scale 5000

Writes only into bench-* namespaces. Never touches `memories` or Grok's records.
Sends phase-screenshot commands to the already-running Grok Observer recorder.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import traceback
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Allow `python -m benchmark.run` from the repo root.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmark.adapters.ember_rest import EmberRestAdapter
from benchmark.util import (
    JsonlLog, dump_json, load_jsonl, now_iso, now_wat, percentiles,
    ranked_ids, recall_at, reciprocal_rank, run_stamp, to_wat_label, wat_label,
)

PROTECTED_NAMESPACES = {"memories"}  # Grok's live namespace — never write/touch
GROK_AGENT = "agent-bd98fca7-0c95-45df-a55f-063e41bc6831"
CTRL = Path("/tmp/bench-recorder-ctrl")
WAL = Path("/workspace/embers-diaries/ember_store/wal.jsonl")
LOGS = [
    Path("/tmp/uvicorn.log"),          # live process stdout (current pid)
    Path("/tmp/embers-uvicorn.log"),  # earlier process today (Samuel's named path)
]


def recorder_cmd(op: str, label: str = "") -> None:
    CTRL.mkdir(parents=True, exist_ok=True)
    name = f"cmd-{time.time_ns()}-{op}.json"
    (CTRL / name).write_text(json.dumps({"op": op, "label": label}))


def health_probe(adapter: EmberRestAdapter, n: int = 7, pause: float = 0.05) -> dict:
    samples = []
    for _ in range(n):
        r = adapter.health()
        samples.append(r)
        time.sleep(pause)
    ok = [s["latency_ms"] for s in samples if s.get("ok")]
    bad = [s for s in samples if not s.get("ok")]
    return {
        "ts": now_iso(),
        "ok_count": len(ok),
        "fail_count": len(bad),
        "latencies": percentiles(ok),
        "statuses": [s.get("status") for s in samples],
    }


def snapshot_passive(tag: str, dest: Path) -> dict:
    """Read-only snapshot of WAL + uvicorn logs. No memory contents, no tokens, no IPs."""
    import re
    from collections import Counter

    def redact_line(s: str) -> str:
        s = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "<ip>", s)
        s = re.sub(r"([0-9a-f]{0,4}:){2,7}[0-9a-f:.]+", "<ip6>", s, flags=re.I)
        s = re.sub(r"(token|authorization|x-ember-token)[\"'=\s:]+[^\s,&#\"']+", r"\1=<redacted>", s, flags=re.I)
        s = re.sub(r"EMBER-[A-Z0-9-]+", "EMBER-<code>", s)
        return s

    snap = {"tag": tag, "ts": now_iso(), "wat": wat_label()}
    # WAL aggregates
    wal = {"exists": WAL.exists(), "bytes": WAL.stat().st_size if WAL.exists() else 0, "lines": 0}
    grok_ops = Counter()
    grok_ns = Counter()
    grok_types = Counter()
    grok_times = []
    if WAL.exists():
        n = 0
        with WAL.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                n += 1
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                data = o.get("data") if isinstance(o.get("data"), dict) else {}
                aid = data.get("agent_id") or data.get("written_by")
                if aid == GROK_AGENT:
                    grok_ops[str(o.get("operation") or "status_only")] += 1
                    grok_ns[str(data.get("namespace") or "?")] += 1
                    grok_types[str(data.get("record_type") or "?")] += 1
                    if o.get("timestamp"):
                        grok_times.append(o["timestamp"])
        wal["lines"] = n
    wal["grok"] = {
        "agent_id": GROK_AGENT,
        "registered_name": "grok-demo-agent",
        "ops": dict(grok_ops),
        "namespaces": dict(grok_ns),
        "record_types": dict(grok_types),
        "n_events": sum(grok_ops.values()),
        "first_ts_utc": grok_times[0] if grok_times else None,
        "last_ts_utc": grok_times[-1] if grok_times else None,
        "first_ts_wat": to_wat_label(grok_times[0]) if grok_times else None,
        "last_ts_wat": to_wat_label(grok_times[-1]) if grok_times else None,
    }
    snap["wal"] = wal

    log_info = []
    for p in LOGS:
        info = {"path": str(p), "exists": p.exists()}
        if p.exists():
            st = p.stat()
            info["bytes"] = st.st_size
            info["mtime_wat"] = to_wat_label(st.st_mtime)
            text = p.read_text(errors="replace")
            lines = text.splitlines()
            info["lines"] = len(lines)
            paths = Counter()
            statuses = Counter()
            for L in lines:
                m = re.search(r'"([A-Z]+) ([^"]+) HTTP/[\d.]+"\s+(\d+)', L)
                if m:
                    method, path, stcode = m.group(1), m.group(2).split("?")[0], m.group(3)
                    paths[f"{method} {path}"] += 1
                    statuses[stcode] += 1
            info["paths"] = dict(paths)
            info["statuses"] = dict(statuses)
            info["has_timing"] = bool(re.search(r"\d+(\.\d+)?ms", text))
        log_info.append(info)
    snap["logs"] = log_info
    dest.parent.mkdir(parents=True, exist_ok=True)
    dump_json(dest, snap)
    return snap


def write_body(mem: dict, namespace: str) -> dict:
    return {
        "content": mem["content"],
        "subject": mem["subject"],
        "namespace": namespace,
        "tags": mem.get("tags") or [],
        "memory_type": mem.get("memory_type") or "raw",
        "room": mem.get("room") or "task",
        "creation_reason": f"benchmark:{mem['uid']}:{mem['kind']}",
    }


def extract_batch_map(batch_result: dict, uids: list[str]) -> dict[str, str]:
    """Map dataset uids to Ember record ids from a batch response."""
    mapping = {}
    body = batch_result.get("body") or batch_result.get("batch") or {}
    rows = body.get("results") if isinstance(body, dict) else []
    if not isinstance(rows, list):
        rows = []
    for uid, row in zip(uids, rows):
        if isinstance(row, dict) and row.get("ok") and row.get("id"):
            mapping[uid] = row["id"]
    return mapping


def score_queries(adapter, queries, uid_to_id, namespace, log: JsonlLog, top_k=10, tag=""):
    per_type = defaultdict(list)
    rows = []
    for q in queries:
        relevant = {uid_to_id[u] for u in q["relevant_uids"] if u in uid_to_id}
        if not relevant:
            continue
        r = adapter.recall(q["query"], namespace=namespace, top_k=top_k)
        ids = ranked_ids(r.get("body"))
        rec1 = recall_at(ids, relevant, 1)
        rec5 = recall_at(ids, relevant, 5)
        rec10 = recall_at(ids, relevant, 10)
        mrr = reciprocal_rank(ids, relevant)
        irr = q.get("irrelevant_uids") or []
        irr_ids = {uid_to_id[u] for u in irr if u in uid_to_id}
        distractor_hit = any(i in irr_ids for i in ids[:1]) if irr_ids else None
        row = {
            "qid": q["qid"],
            "type": q["type"],
            "ok": r.get("ok"),
            "status": r.get("status"),
            "latency_ms": r.get("latency_ms"),
            "recall@1": rec1,
            "recall@5": rec5,
            "recall@10": rec10,
            "mrr": mrr,
            "n_returned": len(ids),
            "distractor_in_top1": distractor_hit,
            "tag": tag,
        }
        log.write({"op": "recall", **row, "namespace": namespace})
        rows.append(row)
        per_type[q["type"]].append(row)
    def agg(rs):
        if not rs:
            return None
        n = len(rs)
        return {
            "n": n,
            "recall@1": sum(x["recall@1"] for x in rs) / n,
            "recall@5": sum(x["recall@5"] for x in rs) / n,
            "recall@10": sum(x["recall@10"] for x in rs) / n,
            "mrr": sum(x["mrr"] for x in rs) / n,
            "latency": percentiles(x["latency_ms"] for x in rs if x.get("latency_ms") is not None),
            "errors": sum(1 for x in rs if not x.get("ok")),
        }
    overall = agg(rows)
    by_type = {t: agg(rs) for t, rs in per_type.items()}
    return {"overall": overall, "by_type": by_type, "n_scored": len(rows)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="ember-rest")
    ap.add_argument("--scale", type=int, default=5000)
    ap.add_argument("--checkpoints", default="1000,2500,5000")
    ap.add_argument("--base-url", default="http://localhost:9200")
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--batch-size", type=int, default=100)
    ap.add_argument("--dataset", default=str(Path(__file__).parent / "dataset"))
    ap.add_argument("--results", default=str(Path(__file__).parent / "results"))
    ap.add_argument("--skip-register", action="store_true")
    args = ap.parse_args()

    if args.target != "ember-rest":
        if args.target in {"mem0", "zep", "letta"}:
            print(f"{args.target} adapter is a stub. Not inventing results. Exiting.", file=sys.stderr)
            sys.exit(2)
        print(f"unknown target {args.target}", file=sys.stderr)
        sys.exit(2)

    checkpoints = [int(x) for x in args.checkpoints.split(",") if x.strip()]
    checkpoints = [c for c in checkpoints if c <= args.scale]
    if args.scale not in checkpoints:
        checkpoints.append(args.scale)
    checkpoints = sorted(set(checkpoints))

    stamp = run_stamp()
    run_dir = Path(args.results) / f"run-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    secrets = Path(__file__).parent / ".secrets"
    secrets.mkdir(parents=True, exist_ok=True)
    (secrets / ".gitignore").write_text("*\n")

    mems = load_jsonl(Path(args.dataset) / "memories.jsonl")[: args.scale]
    queries = load_jsonl(Path(args.dataset) / "queries.jsonl")

    ns_scale = f"bench-2026-11-13-{stamp}-scale"
    ns_features = f"bench-2026-11-13-{stamp}-features"
    if ns_scale.startswith("memories") or "memories" == ns_scale:
        raise SystemExit("refusing to use memories namespace")

    adapter = EmberRestAdapter(args.base_url, timeout=60.0)
    log = JsonlLog(run_dir / "raw.jsonl")
    meta = {
        "run_id": f"run-{stamp}",
        "started_at_wat": wat_label(),
        "started_at": now_iso(),
        "host": socket.gethostname(),
        "base_url": args.base_url,
        "target": args.target,
        "scale": args.scale,
        "checkpoints": checkpoints,
        "concurrency": args.concurrency,
        "batch_size": args.batch_size,
        "namespaces": {"scale": ns_scale, "features": ns_features},
        "git_sha_base": "f05ed04",
        "note": "Live Ember. Writes only to bench-* . Grok/memories never written.",
    }
    dump_json(run_dir / "meta.json", meta)
    print(f"RUN {meta['run_id']} ns={ns_scale} scale={args.scale} started {meta['started_at_wat']}", flush=True)

    # --- register ---
    reg = adapter.register("bench-ember-2026-11-13", "benchmark-harness", "ember-bench-1")
    log.write({"op": "register", "status": reg.get("status"), "latency_ms": reg.get("latency_ms"), "ok": reg.get("ok")})
    if not reg.get("ok"):
        print("REGISTER FAILED", reg.get("status"), redact_body(reg.get("body")), flush=True)
        sys.exit(1)
    body = reg["body"]
    agent_id = body["agent_id"]
    token = body["token"]
    # Persist credentials privately, never to the run dir that will be committed.
    (secrets / "agent.json").write_text(json.dumps({
        "agent_id": agent_id, "token": token, "name": body.get("name"),
        "registered_at": now_iso(),
    }))
    os.chmod(secrets / "agent.json", 0o600)
    adapter.set_auth(agent_id, token)
    print(f"registered agent_id={agent_id} (token not printed)", flush=True)

    # --- collision check (names only; do not fetch records of non-bench ns) ---
    listed = adapter.list_namespaces()
    log.write({"op": "list_namespaces", "status": listed.get("status"), "latency_ms": listed.get("latency_ms")})
    names = []
    raw_ns = (listed.get("body") or {}).get("namespaces") if listed.get("ok") else None
    if isinstance(raw_ns, list):
        for item in raw_ns:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict) and "name" in item:
                names.append(item["name"])
    bench_existing = [n for n in names if str(n).startswith("bench-")]
    protected_existing = [n for n in names if n in PROTECTED_NAMESPACES or n == "memories"]
    other_count = len(names) - len(bench_existing)
    dump_json(run_dir / "namespace_collision_check.json", {
        "ok": listed.get("ok"),
        "status": listed.get("status"),
        "bench_existing": bench_existing,
        "protected_present": bool(protected_existing),
        "protected_names": sorted(protected_existing),
        "other_namespace_count": other_count,
        "total_listed": len(names),
        "will_use": [ns_scale, ns_features],
        "note": "Non-bench namespace names other than the known protected 'memories' are not listed here.",
    })
    if ns_scale in names:
        print("WARNING: scale namespace already exists; proceeding with fresh unique stamp anyway", flush=True)

    sess = adapter.start_session("ember-benchmark-scale", ns_scale)
    log.write({"op": "start_session", "status": sess.get("status"), "latency_ms": sess.get("latency_ms"),
               "ok": sess.get("ok"), "session_id": (sess.get("body") or {}).get("session_id")})
    print(f"session={(sess.get('body') or {}).get('session_id')} ns={ns_scale}", flush=True)

    # --- baseline health + grok snapshot ---
    baseline_health = health_probe(adapter)
    dump_json(run_dir / "health-baseline.json", baseline_health)
    print(f"health baseline {baseline_health['latencies']}", flush=True)
    snapshot_passive("before-load", run_dir / "grok" / "snapshot-before-load.json")

    recorder_cmd("screenshot", "load-start-before-writes")
    time.sleep(2)

    uid_to_id: dict[str, str] = {}
    write_latencies_single: list[float] = []
    write_latencies_batch: list[float] = []
    batch_throughput = []
    write_errors = []
    summaries = []
    prev = 0
    baseline_p95 = (baseline_health.get("latencies") or {}).get("p95") or 50.0
    concurrency = args.concurrency

    # A handful of single writes first for single-write latency, then batches.
    single_n = min(20, len(mems))
    print(f"single-write probe n={single_n}", flush=True)
    t_single = time.monotonic()
    for mem in mems[:single_n]:
        r = adapter.write(write_body(mem, ns_scale))
        log.write({"op": "write", "latency_ms": r.get("latency_ms"), "status": r.get("status"),
                   "ok": r.get("ok"), "ids": r.get("ids"), "uid": mem["uid"], "namespace": ns_scale})
        if r.get("ok") and r.get("ids"):
            uid_to_id[mem["uid"]] = r["ids"][0]
            write_latencies_single.append(r["latency_ms"])
        else:
            write_errors.append({"uid": mem["uid"], "status": r.get("status"), "err": r.get("error")})
    single_wall = time.monotonic() - t_single
    print(f"single writes done wall={single_wall:.2f}s p50={percentiles(write_latencies_single)} errors={len(write_errors)}", flush=True)

    def run_one_batch(chunk):
        uids = [m["uid"] for m in chunk]
        bodies = [write_body(m, ns_scale) for m in chunk]
        r = adapter.write_batch(bodies, atomic=False)
        return uids, r

    for cp in checkpoints:
        recorder_cmd("screenshot", f"{cp}-write-start")
        snapshot_passive(f"at-{cp}-write-start", run_dir / "grok" / f"snapshot-{cp}-write-start.json")
        # remaining memories in (prev, cp)
        todo = [m for m in mems[single_n:] if prev <= m["index"] < cp] if prev == 0 else [m for m in mems if prev <= m["index"] < cp]
        # first checkpoint includes those already written singly — skip them
        if prev == 0:
            todo = [m for m in mems if m["index"] >= single_n and m["index"] < cp]
        print(f"=== writing to {cp}: {len(todo)} remaining (have {len(uid_to_id)}) conc={concurrency} ===", flush=True)
        t_cp = time.monotonic()
        mid_signaled = False
        # batches
        batches = [todo[i:i + args.batch_size] for i in range(0, len(todo), args.batch_size)]
        for bi, chunk in enumerate(batches):
            # health throttle every 5 batches
            if bi and bi % 5 == 0:
                h = health_probe(adapter, n=5)
                p95 = (h.get("latencies") or {}).get("p95") or 0
                log.write({"op": "health", "phase": f"to-{cp}", "batch": bi, **h})
                if p95 and (p95 > 200 or (baseline_p95 and p95 > 5 * baseline_p95)):
                    print(f"HEALTH backoff p95={p95}ms baseline_p95={baseline_p95}; sleeping 2s, conc=1", flush=True)
                    concurrency = 1
                    time.sleep(2)
            t0 = time.monotonic()
            uids, r = run_one_batch(chunk)
            wall_ms = (time.monotonic() - t0) * 1000
            write_latencies_batch.append(r.get("latency_ms") or wall_ms)
            mapping = extract_batch_map(r, uids)
            uid_to_id.update(mapping)
            ok_n = len(mapping)
            fail_n = len(uids) - ok_n
            if fail_n:
                body = r.get("body") or {}
                rows = body.get("results") if isinstance(body, dict) else []
                for uid, row in zip(uids, rows or []):
                    if not (isinstance(row, dict) and row.get("ok") and row.get("id")):
                        write_errors.append({"uid": uid, "row": {k: row.get(k) for k in ("ok", "error", "status") if isinstance(row, dict)}})
            rate = ok_n / max((time.monotonic() - t0), 1e-6)
            batch_throughput.append(rate)
            log.write({
                "op": "write-batch", "latency_ms": r.get("latency_ms"), "wall_ms": wall_ms,
                "status": r.get("status"), "ok": r.get("ok"), "ids": r.get("ids"),
                "n_ok": ok_n, "n_fail": fail_n, "namespace": ns_scale,
                "checkpoint": cp, "batch_index": bi, "writes_per_s": rate,
            })
            print(f"  batch {bi+1}/{len(batches)} ok={ok_n} fail={fail_n} lat={r.get('latency_ms'):.0f}ms {rate:.1f}/s total={len(uid_to_id)}", flush=True)
            if not mid_signaled and len(uid_to_id) >= (prev + cp) // 2:
                recorder_cmd("screenshot", f"{cp}-write-mid")
                mid_signaled = True
            time.sleep(0.05)
        # catch any leftover in [prev, cp) not written (e.g. if single_n covered some)
        missing = [m for m in mems if m["index"] < cp and m["uid"] not in uid_to_id]
        if missing:
            print(f"  filling {len(missing)} missing before checkpoint", flush=True)
            for i in range(0, len(missing), args.batch_size):
                uids, r = run_one_batch(missing[i:i + args.batch_size])
                uid_to_id.update(extract_batch_map(r, uids))
                log.write({"op": "write-batch-fill", "status": r.get("status"), "latency_ms": r.get("latency_ms"),
                           "ids": r.get("ids"), "checkpoint": cp})

        write_wall = time.monotonic() - t_cp
        health_cp = health_probe(adapter)
        recorder_cmd("screenshot", f"{cp}-writes-done")
        snapshot_passive(f"at-{cp}-writes-done", run_dir / "grok" / f"snapshot-{cp}-writes-done.json")

        # Apply supersession updates whose v1 is already written, once.
        # Uses MCP later if REST lacks content_hash; try REST read first, then a raw GET.
        applied = apply_supersessions(adapter, mems, uid_to_id, queries, log, ns_scale, cp)

        q_valid = [q for q in queries if q["min_index"] < cp]
        # For supersession, only score those we've updated
        print(f"  scoring {len(q_valid)} queries at scale={cp} (written={len(uid_to_id)})", flush=True)
        t_sc = time.monotonic()
        scores = score_queries(adapter, q_valid, uid_to_id, ns_scale, log, tag=f"scale-{cp}")
        score_wall = time.monotonic() - t_sc
        summary = {
            "checkpoint": cp,
            "written": len(uid_to_id),
            "write_wall_s": write_wall,
            "score_wall_s": score_wall,
            "write_errors_so_far": len(write_errors),
            "single_write_latency": percentiles(write_latencies_single),
            "batch_write_latency": percentiles(write_latencies_batch),
            "batch_writes_per_s": percentiles(batch_throughput),
            "health": health_cp,
            "recall": scores,
            "supersessions_applied": applied,
            "ts": now_iso(),
            "wat": wat_label(),
        }
        dump_json(run_dir / f"summary-{cp}.json", summary)
        summaries.append(summary)
        ov = scores.get("overall") or {}
        print(
            f"=== {cp} written={len(uid_to_id)} "
            f"R@1={ov.get('recall@1')} R@5={ov.get('recall@5')} R@10={ov.get('recall@10')} "
            f"MRR={ov.get('mrr')} recall_lat={ov.get('latency')} health_p95={(health_cp.get('latencies') or {}).get('p95')} ===",
            flush=True,
        )
        recorder_cmd("screenshot", f"{cp}-recall-done")
        prev = cp

    snapshot_passive("after-load", run_dir / "grok" / "snapshot-after-load.json")
    recorder_cmd("screenshot", "load-complete")

    final = {
        "meta": meta,
        "finished_at": now_iso(),
        "finished_at_wat": wat_label(),
        "written": len(uid_to_id),
        "write_errors": len(write_errors),
        "single_write_latency": percentiles(write_latencies_single),
        "batch_write_latency": percentiles(write_latencies_batch),
        "batch_writes_per_s": percentiles(batch_throughput),
        "checkpoints": summaries,
        "agent_id": agent_id,
        "namespaces": {"scale": ns_scale, "features": ns_features},
        "id_map_path": str(run_dir / "uid_to_id.json"),
    }
    dump_json(run_dir / "uid_to_id.json", uid_to_id)
    dump_json(run_dir / "write_errors.json", write_errors[:200])
    dump_json(run_dir / "summary.json", final)
    log.close()
    adapter.close()
    print(f"DONE written={len(uid_to_id)} errors={len(write_errors)} dir={run_dir}", flush=True)
    print(json.dumps({
        "run_dir": str(run_dir),
        "written": len(uid_to_id),
        "checkpoints": [
            {"cp": s["checkpoint"], "recall": s["recall"].get("overall"), "batch_lat": s["batch_write_latency"]}
            for s in summaries
        ],
    }, default=str, indent=2), flush=True)


def redact_body(body):
    if not isinstance(body, dict):
        return body
    return {k: ("<redacted>" if "token" in k.lower() else v) for k, v in body.items()}


def apply_supersessions(adapter, mems, uid_to_id, queries, log, namespace, cp) -> dict:
    """Try to CAS-update supersede_v1 memories already written. Hash source may fail; record evidence."""
    targets = [m for m in mems if m["kind"] == "supersede_v1" and m["index"] < cp and m["uid"] in uid_to_id]
    applied = 0
    failed = []
    remapped = 0
    for m in targets:
        if m.get("_updated"):
            applied += 1
            continue
        rid = uid_to_id[m["uid"]]
        # REST memory_read may omit content_hash — try several paths.
        expected = None
        src = None
        read = adapter.read(rid)
        body = read.get("body") or {}
        for key in ("content_hash", "hash", "expected_hash"):
            if isinstance(body, dict) and body.get(key):
                expected = body[key]
                src = f"read.{key}"
                break
        if not expected and isinstance(body, dict):
            # maybe nested
            for nest in (body.get("record"), body.get("data")):
                if isinstance(nest, dict) and nest.get("content_hash"):
                    expected = nest["content_hash"]
                    src = "read.nested"
                    break
        if not expected:
            # GET /records/{id} legacy
            legacy = adapter.raw("GET", f"/records/{rid}")
            lb = legacy.get("body") or {}
            if isinstance(lb, dict) and lb.get("content_hash"):
                expected = lb["content_hash"]
                src = "legacy_records"
        log.write({"op": "supersede_hash_lookup", "uid": m["uid"], "rid": rid,
                   "found": bool(expected), "src": src, "read_status": read.get("status")})
        if not expected:
            failed.append({"uid": m["uid"], "reason": "no_content_hash_via_rest"})
            continue
        new_data = {"content": m["update_to"], "subject": m["subject"]}
        upd = adapter.update(rid, new_data, expected, creation_reason=f"benchmark-supersede:{m['uid']}")
        log.write({"op": "update", "uid": m["uid"], "rid": rid, "status": upd.get("status"),
                   "latency_ms": upd.get("latency_ms"), "ok": upd.get("ok"),
                   "new_id": (upd.get("body") or {}).get("record_id") if isinstance(upd.get("body"), dict) else None})
        if upd.get("ok") and isinstance(upd.get("body"), dict) and upd["body"].get("record_id"):
            new_id = upd["body"]["record_id"]
            uid_to_id[m["uid"]] = new_id  # current version wins
            uid_to_id[m["uid"] + "#old"] = rid
            m["_updated"] = True
            applied += 1
            remapped += 1
        else:
            failed.append({"uid": m["uid"], "status": upd.get("status"), "body_keys": list((upd.get("body") or {}).keys()) if isinstance(upd.get("body"), dict) else None})
    return {"targets": len(targets), "applied": applied, "failed": len(failed), "failures_sample": failed[:8], "remapped": remapped}


if __name__ == "__main__":
    main()
