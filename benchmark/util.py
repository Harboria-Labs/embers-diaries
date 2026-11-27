"""Shared helpers: clocks (WAT), redaction, percentiles, JSONL logging."""

from __future__ import annotations

import json
import math
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

WAT = timezone(timedelta(hours=1), name="WAT")

# Fields that must never appear in committed logs or reports.
_SECRET_KEYS = re.compile(
    r"(token|authorization|password|secret|credential|api[_-]?key|x-ember-token"
    r"|access_code|view_code|observer_code|cookie)",
    re.I,
)
_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_IP6 = re.compile(r"\b(?:[0-9a-f]{0,4}:){2,7}[0-9a-f:.]+", re.I)
_EMBER_CODE = re.compile(r"EMBER-[A-Z0-9-]{10,}")
_HEX_CODE = re.compile(r"\b[0-9a-f]{32,}\b")


def now_wat() -> datetime:
    return datetime.now(WAT)


def now_iso() -> str:
    return now_wat().isoformat(timespec="milliseconds")


def run_stamp() -> str:
    return now_wat().strftime("%Y%m%d-%H%M%S")


def wat_label(dt: datetime | None = None) -> str:
    dt = dt or now_wat()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc).astimezone(WAT)
    else:
        dt = dt.astimezone(WAT)
    return dt.strftime("%Y-%m-%d %H:%M:%S WAT")


def to_wat_label(ts: str | float | datetime) -> str:
    """Convert a timestamp (ISO, UNIX, or datetime) to a WAT label."""
    if isinstance(ts, datetime):
        dt = ts
    elif isinstance(ts, (int, float)):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    else:
        text = str(ts).replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return str(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    return wat_label(dt)


def redact(text: str) -> str:
    if not text:
        return text
    text = _IP.sub("<ip>", text)
    text = _IP6.sub("<ip6>", text)
    text = _EMBER_CODE.sub("EMBER-<code>", text)
    text = re.sub(
        r"(token|authorization|x-ember-token|code|password)([\"'=\s:]+)[^\s,&#\"']+",
        r"\1\2<redacted>",
        text,
        flags=re.I,
    )
    return text


def redact_obj(obj: Any, depth: int = 0) -> Any:
    """Drop secret values from a JSON-able object. Never logs tokens or codes."""
    if depth > 12:
        return "<truncated>"
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if _SECRET_KEYS.search(str(k)):
                out[k] = "<redacted>"
            else:
                out[k] = redact_obj(v, depth + 1)
        return out
    if isinstance(obj, list):
        if len(obj) > 200:
            return [redact_obj(x, depth + 1) for x in obj[:200]] + [f"<+{len(obj)-200} more>"]
        return [redact_obj(x, depth + 1) for x in obj]
    if isinstance(obj, str):
        return redact(obj)
    return obj


def percentiles(values: Iterable[float], ps=(50, 95, 99)) -> dict[str, float | None]:
    data = sorted(float(x) for x in values)
    if not data:
        return {f"p{p}": None for p in ps}
    out: dict[str, float | None] = {"n": len(data), "min": data[0], "max": data[-1],
                                    "mean": sum(data) / len(data)}
    for p in ps:
        if not data:
            out[f"p{p}"] = None
            continue
        # Nearest-rank, 1-indexed.
        k = max(1, min(len(data), math.ceil(p / 100 * len(data))))
        out[f"p{p}"] = data[k - 1]
    return out


def ranked_ids(payload: Any) -> list[str]:
    """Extract a ranked list of record ids from a recall/search/query payload."""
    if isinstance(payload, list):
        ids: list[str] = []
        for item in payload:
            if isinstance(item, dict):
                if isinstance(item.get("id"), str):
                    ids.append(item["id"])
                elif isinstance(item.get("record"), dict) and isinstance(item["record"].get("id"), str):
                    ids.append(item["record"]["id"])
                elif isinstance(item.get("record_id"), str):
                    ids.append(item["record_id"])
                else:
                    ids.extend(ranked_ids(item))
            elif isinstance(item, str):
                ids.append(item)
        return _unique(ids)
    if not isinstance(payload, dict):
        return []
    for key in ("results", "memories", "records", "candidates"):
        if key in payload:
            return ranked_ids(payload[key])
    if isinstance(payload.get("id"), str):
        return [payload["id"]]
    return []


def _unique(ids: list[str]) -> list[str]:
    seen = set()
    out = []
    for i in ids:
        if i and i not in seen:
            seen.add(i)
            out.append(i)
    return out


def recall_at(ranked: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return float("nan")
    return 1.0 if any(i in relevant for i in ranked[:k]) else 0.0


def reciprocal_rank(ranked: list[str], relevant: set[str]) -> float:
    for i, rid in enumerate(ranked, 1):
        if rid in relevant:
            return 1.0 / i
    return 0.0


class JsonlLog:
    """Append-only JSONL writer for per-request evidence."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")

    def write(self, row: dict) -> None:
        row = dict(row)
        row.setdefault("ts", now_iso())
        self._fh.write(json.dumps(redact_obj(row), default=str, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def monotonic_ms(start: float | None = None) -> float:
    if start is None:
        return time.monotonic() * 1000
    return (time.monotonic() - start) * 1000


def dump_json(path: Path, obj: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=str, ensure_ascii=False) + "\n", encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows
