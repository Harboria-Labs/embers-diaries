"""Opt-in reflect+consolidate cycle. Off unless interval env is set."""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from ._native import atomic_replace
from .core.record import EmberRecord
from .storage.format import decode, decode_index, encode_index

logger = logging.getLogger(__name__)

_AUDIT_LEASE_SECONDS = 300
_MAX_FINDINGS = 256


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _audit_dir(db) -> Path:
    path = db._store.meta_dir / "integrity_audits"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save_report(db, report: dict) -> None:
    path = _audit_dir(db) / f"{report['run_id']}.json"
    atomic_replace(str(path), encode_index(report))


def _scope(db, namespaces, actor: str, operation: str) -> tuple[str, ...]:
    if (not isinstance(namespaces, list) or not namespaces or
            any(not isinstance(ns, str) or not ns.strip() or ns != ns.strip()
                for ns in namespaces)):
        raise ValueError("namespaces must be a nonempty list of names")
    scope = tuple(dict.fromkeys(namespaces))
    for namespace in scope:
        db.require_namespace_access(namespace, actor, operation)
    return scope


def _finding(report: dict, category: str, reason: str,
             record_id: str | None = None, namespace: str | None = None) -> None:
    report["finding_counts"][category] = report["finding_counts"].get(category, 0) + 1
    if len(report["findings"]) < _MAX_FINDINGS:
        entry = {"category": category, "reason": reason}
        if namespace in report["namespaces"] and record_id is not None:
            entry.update({"namespace": namespace, "record_id": record_id})
        report["findings"].append(entry)
    else:
        report["findings_truncated"] += 1


def _report_path(db, run_id: str) -> Path:
    try:
        run_id = str(uuid.UUID(run_id))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid audit run id") from exc
    return db._store.meta_dir / "integrity_audits" / f"{run_id}.json"


def get_integrity_audit(db, run_id: str, actor: str) -> dict:
    """Read one durable report only when the caller can read its entire scope."""
    path = _report_path(db, run_id)
    if not path.exists():
        raise KeyError("audit run not found")
    report = decode_index(path.read_bytes())
    for namespace in report["namespaces"]:
        db.require_namespace_access(namespace, actor, "read")
    if report["status"] == "running":
        heartbeat = datetime.fromisoformat(report["heartbeat_at"])
        if (_now() - heartbeat).total_seconds() > _AUDIT_LEASE_SECONDS:
            with db._store.lock:
                current = decode_index(path.read_bytes())
                if current["status"] == "running" and current["heartbeat_at"] == report["heartbeat_at"]:
                    current["status"] = "interrupted"
                    current["completed_at"] = _now().isoformat()
                    _save_report(db, current)
                report = current
    if any(action.get("status") == "running" for action in report.get("recovery", [])):
        with db._store.lock:
            current = decode_index(path.read_bytes())
            changed = False
            for action in current.get("recovery", []):
                if action.get("status") == "running" and (
                        _now() - datetime.fromisoformat(action["at"])
                ).total_seconds() > _AUDIT_LEASE_SECONDS:
                    action["status"] = "interrupted"
                    action["completed_at"] = _now().isoformat()
                    changed = True
            if changed:
                _save_report(db, current)
            report = current
    if report["status"] == "complete":
        try:
            if db._store.index_epoch() != report["source_generation_end"]:
                report = dict(report, status="stale", certified=False)
        except Exception:
            report = dict(report, status="stale", certified=False)
    return report


def run_integrity_audit(db, namespaces: list[str], actor: str) -> dict:
    """Scan physical source independently of MasterIndex; never repair source."""
    scope = _scope(db, namespaces, actor, "write")
    run_id = str(uuid.uuid4())
    with db._store.lock:
        directory = _audit_dir(db)
        active_path = directory / "active.json"
        last_run_id = None
        if active_path.exists():
            active = decode_index(active_path.read_bytes())
            last_run_id = active.get("last_run_id")
            if active.get("run_id"):
                old_path = _report_path(db, active["run_id"])
                if old_path.exists():
                    old = decode_index(old_path.read_bytes())
                    if old["status"] == "running" and (
                            _now() - datetime.fromisoformat(old["heartbeat_at"])
                    ).total_seconds() <= _AUDIT_LEASE_SECONDS:
                        raise RuntimeError("another integrity audit is running")
        generation = db._store.index_epoch()
        files = sorted(db._store.records_dir.glob("*.ember"))
        physical_ids = {p.stem for p in files}
        report = {
            "run_id": run_id, "namespaces": list(scope), "initiator": actor,
            "started_at": _now().isoformat(), "heartbeat_at": _now().isoformat(),
            "completed_at": None, "status": "running", "certified": False,
            "source_generation_start": generation, "source_generation_end": None,
            "records_examined": 0, "finding_counts": {}, "findings": [],
            "findings_truncated": 0, "recovery": [],
        }
        _save_report(db, report)
        atomic_replace(str(active_path), encode_index({"run_id": run_id,
                                                      "last_run_id": last_run_id}))
    records: dict[str, EmberRecord] = {}
    unknown_scope = False
    try:
        for path in files:
            rid = path.stem
            try:
                raw = decode(path.read_bytes())
                if not isinstance(raw, dict):
                    raise ValueError("record payload is not a map")
            except FileNotFoundError:
                unknown_scope = True
                _finding(report, "missing_physical", "file disappeared during scan")
                continue
            except Exception:
                unknown_scope = True
                _finding(report, "decode_failure", "physical record cannot be decoded")
                continue
            namespace = raw.get("namespace")
            if not isinstance(namespace, str):
                unknown_scope = True
                _finding(report, "invalid_metadata", "record namespace is unavailable")
                continue
            if namespace not in scope:
                # No unrelated record identity or contents enter a scoped report.
                continue
            report["records_examined"] += 1
            if raw.get("id") != rid:
                _finding(report, "identity_mismatch", "filename and record id differ", rid, namespace)
                continue
            if any(field not in raw for field in ("record_type", "created_at", "data")):
                _finding(report, "invalid_metadata", "required source metadata missing", rid, namespace)
                continue
            try:
                record = EmberRecord.from_dict(raw)
            except Exception:
                _finding(report, "invalid_metadata", "record metadata is invalid", rid, namespace)
                continue
            if record.content_hash is None:
                _finding(report, "legacy_unsealed", "legacy record has no content seal", rid, namespace)
            else:
                try:
                    record.verify_integrity()
                except Exception:
                    _finding(report, "hash_mismatch", "record content seal differs", rid, namespace)
            records[rid] = record
            if report["records_examined"] % 64 == 0:
                report["heartbeat_at"] = _now().isoformat()
                _save_report(db, report)

        # A scoped index comparison is diagnostic only: it never invokes the
        # MasterIndex health check, which could reconcile the very drift audited.
        index_path = db._path / "indexes" / "master.json"
        try:
            index = decode_index(index_path.read_bytes())
            indexed = index.get("records", {})
            if not isinstance(indexed, dict):
                raise ValueError("index records are invalid")
            if index.get("indexed_epoch") != generation:
                _finding(report, "derived_index_inconsistency", "source and indexed generations differ")
        except Exception:
            index = {}
            indexed = {}
            _finding(report, "derived_index_inconsistency", "MasterIndex snapshot is unreadable")
        for rid, record in records.items():
            meta = indexed.get(rid)
            if meta is None:
                _finding(report, "derived_index_inconsistency", "source record absent from index", rid, record.namespace)
            elif (meta.get("namespace") != record.namespace or
                  meta.get("record_type") != record.record_type.value):
                _finding(report, "derived_index_inconsistency", "index namespace or type differs", rid, record.namespace)
        for rid, meta in indexed.items():
            if isinstance(meta, dict) and meta.get("namespace") in scope and rid not in records:
                # Index-only IDs are not trusted as proof of ownership, so do
                # not disclose them to a namespace-scoped agent.
                category = "missing_physical" if rid not in physical_ids else "derived_index_inconsistency"
                _finding(report, category, "index-only candidate: missing source or obsolete metadata; manual source review required")

        children: dict[str, list[str]] = defaultdict(list)
        for rid, record in records.items():
            for source_id in record.derived_from:
                if source_id not in physical_ids:
                    _finding(report, "missing_source_reference", "derivation source is absent", rid, record.namespace)
            if record.supersedes:
                children[record.supersedes].append(rid)
                parent = records.get(record.supersedes)
                if parent is None:
                    _finding(report, "inconsistent_lineage", "version parent is missing or outside scope", rid, record.namespace)
                elif (record.version != parent.version + 1 or
                      record.parent_hash != parent.content_hash):
                    _finding(report, "inconsistent_lineage", "version or parent hash differs", rid, record.namespace)
        superseded = index.get("superseded", {})
        deprecated = set(index.get("deprecated", []))
        for parent, descendants in children.items():
            namespace = records[parent].namespace if parent in records else None
            sidecar = db._store.root / "supersessions" / f"{parent}.superseded"
            try:
                sidecar_data = decode_index(sidecar.read_bytes())
                if sidecar_data.get("old_id") != parent or sidecar_data.get("new_id") not in descendants:
                    raise ValueError("wrong supersession sidecar")
            except Exception:
                _finding(report, "inconsistent_lineage", "supersession sidecar is missing or inconsistent", parent, namespace)
            if superseded.get(parent) not in descendants:
                _finding(report, "derived_index_inconsistency", "index supersession differs", parent, namespace)
        for rid, record in records.items():
            sidecar = db._store.root / "deprecations" / f"{rid}.deprecated"
            if sidecar.exists():
                try:
                    data = decode_index(sidecar.read_bytes())
                    if data.get("record_id") != rid:
                        raise ValueError("wrong deprecation sidecar")
                except Exception:
                    _finding(report, "inconsistent_lineage", "deprecation sidecar is invalid", rid, record.namespace)
                if rid not in deprecated:
                    _finding(report, "derived_index_inconsistency", "index deprecation is missing", rid, record.namespace)
            elif rid in deprecated:
                _finding(report, "derived_index_inconsistency", "index has obsolete deprecation", rid, record.namespace)
        for sidecar in (db._store.root / "supersessions").glob("*.superseded"):
            if sidecar.stem in records and sidecar.stem not in children:
                _finding(report, "inconsistent_lineage", "orphan supersession sidecar", sidecar.stem, records[sidecar.stem].namespace)
            elif sidecar.stem not in physical_ids:
                _finding(report, "inconsistent_lineage", "supersession sidecar has no physical source")
        for sidecar in (db._store.root / "deprecations").glob("*.deprecated"):
            if sidecar.stem not in physical_ids:
                _finding(report, "inconsistent_lineage", "deprecation sidecar has no physical source")
        for parent in records:
            if parent not in children and parent in superseded:
                _finding(report, "derived_index_inconsistency", "index has obsolete supersession", parent, records[parent].namespace)

        end_generation = db._store.index_epoch()
        report["source_generation_end"] = end_generation
        if end_generation != generation or {p.stem for p in db._store.records_dir.glob("*.ember")} != physical_ids:
            report["status"] = "stale"
        else:
            report["status"] = "complete"
        report["certified"] = (report["status"] == "complete" and
                               not report["finding_counts"] and not unknown_scope)
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: audit scan failed"
        logger.exception("integrity audit %s failed", run_id)
    finally:
        report["completed_at"] = _now().isoformat()
        report["heartbeat_at"] = report["completed_at"]
        _save_report(db, report)
        with db._store.lock:
            active_path = _audit_dir(db) / "active.json"
            active = decode_index(active_path.read_bytes())
            if active.get("run_id") == run_id:
                atomic_replace(str(active_path), encode_index({"run_id": None,
                                                              "last_run_id": run_id}))
    return report


def recover_master_index(db, run_id: str, *, local_operator: bool = False) -> dict:
    """Local operator recovery only; no ordinary agent repair authority exists."""
    if not local_operator:
        raise PermissionError("index recovery requires local operator authority")
    path = _report_path(db, run_id)
    with db._store.lock:
        report = decode_index(path.read_bytes())
        categories = set(report["finding_counts"])
        if report["status"] != "complete" or categories - {"derived_index_inconsistency"}:
            raise ValueError("source findings or incomplete audit prohibit index recovery")
        if db._store.index_epoch() != report["source_generation_end"]:
            raise ValueError("audit source generation is stale")
        if any(a.get("status") == "running" for a in report.get("recovery", [])):
            raise RuntimeError("index recovery is already running")
        _scope(db, report["namespaces"], report["initiator"], "write")
        action = {"at": _now().isoformat(), "action": "rebuild_master_index",
                  "status": "running"}
        report["recovery"].append(action)
        _save_report(db, report)
    try:
        with db._store.lock:
            db._reconcile_master_index()
        followup = run_integrity_audit(db, report["namespaces"], report["initiator"])
        action.update({"status": "complete", "followup_run_id": followup["run_id"],
                       "followup_status": followup["status"],
                       "followup_certified": followup["certified"]})
        return followup
    except Exception as exc:
        action.update({"status": "failed", "error": f"{type(exc).__name__}: index recovery failed"})
        raise
    finally:
        action["completed_at"] = _now().isoformat()
        with db._store.lock:
            current = decode_index(path.read_bytes())
            for recorded in current["recovery"]:
                if recorded["at"] == action["at"]:
                    recorded.update(action)
                    break
            _save_report(db, current)


def run_maintenance_cycle(protocol, namespaces: list[str]) -> dict:
    results: dict[str, dict] = {}
    for ns in namespaces:
        entry: dict = {}
        try:
            annotations = protocol.reflect(namespace=ns)
            entry["reflections"] = len(annotations)
        except Exception as e:
            entry["reflect_error"] = f"{type(e).__name__}: {e}"
            logger.warning("maintenance: reflect(%s) failed: %s", ns, e)
        try:
            consolidated = protocol.consolidate(namespace=ns)
            entry["consolidated"] = len(consolidated)
        except Exception as e:
            entry["consolidate_error"] = f"{type(e).__name__}: {e}"
            logger.warning("maintenance: consolidate(%s) failed: %s", ns, e)
        results[ns] = entry
    return results


async def maintenance_loop(protocol, namespaces: list[str], interval_seconds: int):
    import asyncio
    while True:
        try:
            summary = run_maintenance_cycle(protocol, namespaces)
            logger.info("maintenance cycle: %s", summary)
        except Exception as e:
            logger.warning("maintenance cycle raised unexpectedly: %s", e)
        await asyncio.sleep(interval_seconds)
