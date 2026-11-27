"""Ember REST adapter (localhost by default). Never logs tokens."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from .base import MemoryAdapter, TimedResult


class EmberRestAdapter(MemoryAdapter):
    name = "ember-rest"

    def __init__(
        self,
        base_url: str = "http://localhost:9200",
        *,
        timeout: float = 60.0,
        agent_id: str | None = None,
        token: str | None = None,
        session_id: str | None = None,
        headers: dict | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.agent_id = agent_id
        self._token = token
        self.session_id = session_id
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers={"Accept": "application/json", **(headers or {})},
            follow_redirects=True,
        )

    # -- auth -----------------------------------------------------------------
    def set_auth(self, agent_id: str, token: str, session_id: str | None = None) -> None:
        self.agent_id = agent_id
        self._token = token
        if session_id:
            self.session_id = session_id

    def _headers(self, extra: dict | None = None, *, auth: bool = True) -> dict:
        h = {"Content-Type": "application/json"}
        if auth and self.agent_id and self._token:
            h["X-Ember-Agent-Id"] = self.agent_id
            h["X-Ember-Token"] = self._token
        if auth and self.session_id:
            h["X-Ember-Session-Id"] = self.session_id
        if extra:
            h.update(extra)
        return h

    def raw(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict | None = None,
        auth: bool = True,
        headers: dict | None = None,
        raw_body: bytes | None = None,
        content_type: str | None = None,
        timeout: float | None = None,
    ) -> TimedResult:
        """Issue one HTTP request and return status, latency, body (no secrets)."""
        hdrs = self._headers(headers, auth=auth)
        if content_type:
            hdrs["Content-Type"] = content_type
        start = time.monotonic()
        error = None
        status = 0
        body: Any = None
        text = ""
        try:
            kwargs: dict[str, Any] = {"headers": hdrs, "params": params}
            if raw_body is not None:
                kwargs["content"] = raw_body
            elif json_body is not None:
                kwargs["json"] = json_body
            if timeout is not None:
                kwargs["timeout"] = timeout
            resp = self._client.request(method.upper(), path, **kwargs)
            status = resp.status_code
            text = resp.text
            try:
                body = resp.json()
            except Exception:
                body = {"_text": text[:2000]}
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            status = 0
        latency_ms = (time.monotonic() - start) * 1000
        ok = error is None and 200 <= status < 300
        return TimedResult(
            {
                "ok": ok,
                "status": status,
                "latency_ms": round(latency_ms, 3),
                "body": body,
                "error": error,
                "path": path,
                "method": method.upper(),
            }
        )

    # -- high-level -----------------------------------------------------------
    def health(self) -> dict:
        return self.raw("GET", "/health", auth=False)

    def register(self, name: str, provider: str, model: str) -> TimedResult:
        return self.raw(
            "POST",
            "/v1/agents/register",
            json_body={"name": name, "provider": provider, "model": model},
            auth=False,
        )

    def start_session(self, task: str, namespace: str) -> TimedResult:
        result = self.raw(
            "POST",
            "/v1/sessions",
            json_body={"task": task, "namespace": namespace},
        )
        if result.ok and isinstance(result.get("body"), dict):
            sid = result["body"].get("session_id")
            if sid:
                self.session_id = sid
        return result

    def write(self, record: dict) -> TimedResult:
        body = dict(record)
        if self.session_id and "session_id" not in body:
            body["session_id"] = self.session_id
        result = self.raw("POST", "/v1/memory/write", json_body=body)
        if result.ok and isinstance(result.get("body"), dict):
            result["ids"] = [result["body"]["id"]] if result["body"].get("id") else []
        else:
            result["ids"] = []
        return result

    def write_batch(self, records: list[dict], *, atomic: bool = False) -> TimedResult:
        items = []
        for rec in records:
            item = dict(rec)
            if self.session_id and "session_id" not in item:
                item["session_id"] = self.session_id
            items.append(item)
        result = self.raw(
            "POST",
            "/v1/memory/write-batch",
            json_body={"records": items, "atomic": atomic},
        )
        ids = []
        body = result.get("body") or {}
        if isinstance(body, dict):
            for row in body.get("results") or []:
                if isinstance(row, dict) and row.get("ok") and row.get("id"):
                    ids.append(row["id"])
        result["ids"] = ids
        result["batch"] = body
        return result

    def recall(self, query: str, *, namespace: str, top_k: int = 10, **kwargs) -> TimedResult:
        body = {"query": query, "namespace": namespace, "top_k": top_k}
        body.update({k: v for k, v in kwargs.items() if v is not None})
        return self.raw("POST", "/v1/memory/recall", json_body=body)

    def orient(self, clues: str, *, namespace: str, **kwargs) -> TimedResult:
        body = {"clues": clues, "namespace": namespace}
        body.update({k: v for k, v in kwargs.items() if v is not None})
        return self.raw("POST", "/v1/memory/orient", json_body=body)

    def read(self, record_id: str, **params) -> TimedResult:
        return self.raw("GET", f"/v1/memory/read/{record_id}", params=params or None)

    def update(self, record_id: str, data: dict, expected_hash: str, **kwargs) -> TimedResult:
        body = {"data": data, "expected_hash": expected_hash}
        if self.session_id:
            body.setdefault("session_id", self.session_id)
        body.update(kwargs)
        return self.raw("PUT", f"/v1/memory/{record_id}", json_body=body)

    def history(self, record_id: str) -> TimedResult:
        return self.raw("GET", f"/v1/memory/history/{record_id}")

    def graph(self, record_id: str, depth: int = 1) -> TimedResult:
        return self.raw("GET", f"/v1/memory/graph/{record_id}", params={"depth": depth})

    def lifecycle(self, record_id: str) -> TimedResult:
        return self.raw("GET", f"/v1/memory/{record_id}/lifecycle")

    def feedback(self, memory_id: str, outcome: str, **kwargs) -> TimedResult:
        body = {"outcome": outcome}
        body.update(kwargs)
        return self.raw("POST", f"/v1/memory/{memory_id}/feedback", json_body=body)

    def list_feedback(self, memory_id: str) -> TimedResult:
        return self.raw("GET", f"/v1/memory/{memory_id}/feedback")

    def search(self, q: str, *, namespace: str, top_k: int = 10) -> TimedResult:
        return self.raw("GET", "/v1/memory/search", params={"q": q, "namespace": namespace, "top_k": top_k})

    def query(self, *, namespace: str, **kwargs) -> TimedResult:
        body = {"namespace": namespace}
        body.update(kwargs)
        return self.raw("POST", "/v1/memory/query", json_body=body)

    def list_namespaces(self) -> TimedResult:
        return self.raw("GET", "/namespaces")

    def close(self) -> None:
        self._client.close()
