"""Ember MCP adapter (JSON-RPC over POST /mcp). Auth via session_id after start."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from .base import MemoryAdapter, TimedResult

PROTOCOL = "2025-06-18"


class EmberMcpAdapter(MemoryAdapter):
    name = "ember-mcp"

    def __init__(
        self,
        mcp_url: str = "http://localhost:9200/mcp",
        *,
        timeout: float = 60.0,
        agent_id: str | None = None,
        token: str | None = None,
        session_id: str | None = None,
    ):
        self.mcp_url = mcp_url
        self.timeout = timeout
        self.agent_id = agent_id
        self._token = token
        self.session_id = session_id
        self._id = 0
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "MCP-Protocol-Version": PROTOCOL,
            },
        )
        self._initialized = False

    def set_auth(self, agent_id: str, token: str, session_id: str | None = None) -> None:
        self.agent_id = agent_id
        self._token = token
        if session_id:
            self.session_id = session_id

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def rpc(self, method: str, params: dict | None = None, *, notify: bool = False) -> TimedResult:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if not notify:
            msg["id"] = self._next_id()
        if params is not None:
            msg["params"] = params
        start = time.monotonic()
        error = None
        status = 0
        body: Any = None
        try:
            resp = self._client.post(self.mcp_url, json=msg)
            status = resp.status_code
            if status == 202:
                body = {"_accepted": True}
            else:
                try:
                    body = resp.json()
                except Exception:
                    body = {"_text": resp.text[:2000]}
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        latency_ms = (time.monotonic() - start) * 1000
        ok = error is None and (status == 202 or (200 <= status < 300 and not (isinstance(body, dict) and body.get("error"))))
        parsed = None
        if isinstance(body, dict) and "result" in body:
            parsed = self._unwrap(body["result"])
        return TimedResult({
            "ok": ok,
            "status": status,
            "latency_ms": round(latency_ms, 3),
            "body": body,
            "parsed": parsed,
            "error": error if error else (body.get("error") if isinstance(body, dict) else None),
            "method": method,
        })

    @staticmethod
    def _unwrap(result: Any) -> Any:
        # MCP tool results are {content:[{type:text,text:...,}], isError:bool}
        if isinstance(result, dict) and "content" in result:
            texts = [c.get("text") for c in (result.get("content") or []) if isinstance(c, dict)]
            text = "\n".join(t for t in texts if t is not None)
            try:
                return json.loads(text)
            except Exception:
                return {"text": text, "isError": result.get("isError")}
        return result

    def initialize(self) -> TimedResult:
        r = self.rpc("initialize", {
            "protocolVersion": PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "ember-bench", "version": "1.0.0"},
        })
        self.rpc("notifications/initialized", {}, notify=True)
        self._initialized = bool(r.get("ok"))
        return r

    def call_tool(self, name: str, arguments: dict | None = None) -> TimedResult:
        if not self._initialized:
            self.initialize()
        args = dict(arguments or {})
        if self.session_id and "session_id" not in args:
            args["session_id"] = self.session_id
        elif self.agent_id and self._token and "session_id" not in args:
            args.setdefault("agent_id", self.agent_id)
            args.setdefault("token", self._token)
        r = self.rpc("tools/call", {"name": name, "arguments": args})
        parsed = r.get("parsed")
        # isError on tool result
        if isinstance(r.get("body"), dict):
            res = r["body"].get("result")
            if isinstance(res, dict) and res.get("isError"):
                r["ok"] = False
        if isinstance(parsed, dict) and parsed.get("id"):
            r["ids"] = [parsed["id"]]
        elif isinstance(parsed, dict) and parsed.get("record_id"):
            r["ids"] = [parsed["record_id"]]
        return r

    def health(self) -> dict:
        # MCP has no /health; ping.
        return self.rpc("ping")

    def tools_list(self) -> TimedResult:
        if not self._initialized:
            self.initialize()
        return self.rpc("tools/list")

    def start_session(self, task: str, namespace: str) -> TimedResult:
        r = self.call_tool("ember_start_session", {
            "agent_id": self.agent_id,
            "token": self._token,
            "task": task,
            "namespace": namespace,
        })
        parsed = r.get("parsed") or {}
        if isinstance(parsed, dict) and parsed.get("session_id"):
            self.session_id = parsed["session_id"]
        return r

    def write(self, record: dict) -> TimedResult:
        args = dict(record)
        r = self.call_tool("ember_write", args)
        parsed = r.get("parsed") or {}
        if isinstance(parsed, dict) and parsed.get("id"):
            r["ids"] = [parsed["id"]]
        else:
            r["ids"] = []
        return r

    def write_batch(self, records: list[dict], *, atomic: bool = False) -> TimedResult:
        r = self.call_tool("ember_write_batch", {"records": records, "atomic": atomic})
        parsed = r.get("parsed") or {}
        ids = []
        if isinstance(parsed, dict):
            for row in parsed.get("results") or []:
                if isinstance(row, dict) and row.get("ok") and row.get("id"):
                    ids.append(row["id"])
        r["ids"] = ids
        return r

    def recall(self, query: str, *, namespace: str, top_k: int = 10, **kwargs) -> TimedResult:
        args = {"query": query, "namespace": namespace, "top_k": top_k}
        args.update({k: v for k, v in kwargs.items() if v is not None})
        return self.call_tool("ember_recall", args)

    def read(self, record_id: str, **kwargs) -> TimedResult:
        return self.call_tool("ember_read", {"record_id": record_id, **kwargs})

    def visualize(self, *, span_sessions: bool = True, ttl_seconds: int = 14400,
                  namespaces: list[str] | None = None) -> TimedResult:
        args = {"span_sessions": span_sessions, "ttl_seconds": ttl_seconds}
        if namespaces:
            args["namespaces"] = namespaces
        return self.call_tool("ember_visualize", args)

    def visualizer_access(self, namespace: str, ttl_seconds: int = 3600) -> TimedResult:
        return self.call_tool("ember_visualizer_access", {
            "namespace": namespace, "ttl_seconds": ttl_seconds,
        })

    def close(self) -> None:
        self._client.close()
