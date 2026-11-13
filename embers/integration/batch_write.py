"""Best-effort and atomic batches over the single-record write path.

Each item is validated, authorized, and committed by the caller's existing
write function. This module only sequences those calls, rolls a failed atomic
batch back to nothing, and retries a quota refusal one record at a time.
"""

from __future__ import annotations

from typing import Any, Callable

from ..core.errors import BatchQuotaExceeded, StorageLimitError


class BatchLimitError(ValueError):
    """The caller sent more records than ``storage.max_write_batch`` allows."""

    def __init__(self, limit: int, attempted: int):
        self.limit = limit
        self.attempted = attempted
        super().__init__(
            f"batch exceeds max_write_batch ({limit}); received {attempted}")


def structured_error(exc: BaseException) -> dict:
    """Per-item error. Request-level failures never reach this helper."""
    status = getattr(exc, "status_code", None)
    if status is not None and exc.__class__.__name__ == "HTTPException":
        detail = getattr(exc, "detail", str(exc))
        message = detail if isinstance(detail, str) else str(detail)
        return {"type": "HTTPException", "message": message, "status": int(status)}
    if isinstance(exc, StorageLimitError):
        body = exc.to_dict()
        body["type"] = "StorageLimitError"
        body["message"] = str(exc)
        body["status"] = 413 if str(exc.limit).endswith("max_record_bytes") else 507
        return body
    if isinstance(exc, BatchQuotaExceeded) or (
            isinstance(exc, OSError) and "max_total_bytes" in str(exc)):
        return {
            "type": "StorageLimitError",
            "message": str(exc),
            "status": 507,
            "limit": "storage.max_total_bytes",
        }
    if isinstance(exc, PermissionError):
        return {"type": type(exc).__name__, "message": str(exc), "status": 403}
    if isinstance(exc, KeyError):
        key = exc.args[0] if exc.args else str(exc)
        return {"type": "KeyError", "message": str(key), "status": 400}
    if isinstance(exc, (TypeError, ValueError)):
        return {"type": type(exc).__name__, "message": str(exc), "status": 400}
    return {"type": type(exc).__name__, "message": str(exc), "status": 500}


def _atomic_rollback(index: int) -> dict:
    return {
        "index": index,
        "ok": False,
        "error": {
            "type": "AtomicRollback",
            "message": "Batch was not written because another item failed validation.",
            "status": 409,
        },
    }


def run_write_batch(
    db,
    items,
    *,
    atomic: bool,
    limit: int,
    write_item: Callable[[dict], dict],
    measure_item: Callable[[dict, int], dict],
    commit_measured: Callable[[dict], dict],
    after_commit: Callable[[dict, dict], None] | None = None,
    replay: Callable[[Any], dict | None] | None = None,
) -> dict:
    """Run one batch. ``write_item`` is the single-write path.

    ``measure_item`` and ``commit_measured`` are used only for ``atomic``.
    They must build one record and then commit that same record.
    """
    if type(atomic) is not bool:
        raise TypeError("atomic must be a boolean")
    if not isinstance(items, list):
        raise TypeError("records must be a list")
    if len(items) > limit:
        raise BatchLimitError(limit, len(items))

    results: list[dict | None] = [None] * len(items)
    try:
        with db.grouped_writes() as group:
            if atomic:
                _run_atomic(db, group, items, results, measure_item, commit_measured)
            else:
                _run_best_effort(db, group, items, results, write_item)
    except BatchQuotaExceeded as exc:
        _apply_quota(db, results, exc, atomic=atomic, replay=replay)

    if after_commit is not None:
        for index, result in enumerate(results):
            if not result or not result.get("ok"):
                continue
            try:
                after_commit(items[index], result)
            except Exception as exc:
                # The memory is already durable. Keep its id and report the
                # session-log failure on the same item.
                result["ok"] = False
                result["error"] = structured_error(exc)
    return {
        "atomic": atomic,
        "max_batch": limit,
        "count": len(results),
        "results": results,
    }


def _run_best_effort(db, group, items, results, write_item) -> None:
    for index, item in enumerate(items):
        before = len(group.records)
        try:
            if not isinstance(item, dict):
                raise TypeError("record must be an object")
            public = write_item(item)
            results[index] = {"index": index, "ok": True, **public}
        except Exception as exc:
            if len(group.records) > before:
                db.drop_staged_tail(group, before)
            results[index] = {"index": index, "ok": False, "error": structured_error(exc)}


def _run_atomic(db, group, items, results, measure_item, commit_measured) -> None:
    prepared = []
    extra = 0
    failed = False
    for index, item in enumerate(items):
        try:
            if not isinstance(item, dict):
                raise TypeError("record must be an object")
            measured = measure_item(item, extra)
            extra += int(measured["size"])
            prepared.append((index, measured))
        except Exception as exc:
            failed = True
            results[index] = {"index": index, "ok": False, "error": structured_error(exc)}
    if failed:
        group.aborted = True
        for index, _measured in prepared:
            results[index] = _atomic_rollback(index)
        return
    for index, measured in prepared:
        before = len(group.records)
        try:
            public = commit_measured(measured)
            results[index] = {"index": index, "ok": True, **public}
        except Exception as exc:
            if len(group.records) > before:
                db.drop_staged_tail(group, before)
            group.aborted = True
            results[index] = {"index": index, "ok": False, "error": structured_error(exc)}
            for other, _measured in prepared:
                if other != index and results[other] and results[other].get("ok"):
                    results[other] = _atomic_rollback(other)
            for other, _measured in prepared:
                if results[other] is None:
                    results[other] = _atomic_rollback(other)
            break


def _apply_quota(db, results, exc: BatchQuotaExceeded, *, atomic: bool, replay) -> None:
    error = structured_error(exc)
    if atomic or replay is None:
        for index, result in enumerate(results):
            if result and result.get("ok"):
                results[index] = {"index": index, "ok": False, "error": error}
        return

    pending = {record.id: record for record in exc.records}
    for result in results:
        if not result or not result.get("ok"):
            continue
        record = pending.get(result.get("id"))
        if record is None:
            continue
        try:
            extra = replay(record) or {}
            if isinstance(extra, dict):
                result.update(extra)
        except Exception as err:
            result["ok"] = False
            result["error"] = structured_error(err)
            if result.get("id") and not db._store.file_exists(result["id"]):
                result.pop("id", None)
