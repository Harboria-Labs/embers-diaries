"""MCP surface for agent-decided conflicts. No auto-map."""

from __future__ import annotations

from ..core.types import ConflictStatus

_OPEN_QUEUE = {ConflictStatus.OPEN, ConflictStatus.INVESTIGATING}

_RESOLVE_STATUSES = {
    "investigating": ConflictStatus.INVESTIGATING,
    "resolved": ConflictStatus.RESOLVED,
    "accepted_both": ConflictStatus.ACCEPTED_BOTH,
    "dismissed": ConflictStatus.SUPERSEDED,
}

_WRITE_SUBJECT = (
    "Optional. Names the entity this memory is about. "
    "If another row shares the subject and a claim field differs, "
    "write returns possible_conflicts as a hint. Ember does not map "
    "a conflict. Use ember_map_conflict only if you decide it is one. "
    "Different wording in content is not a conflict."
)

_RESOLVE_DESC = (
    "Record a decision on a mapped conflict. Neither memory is deleted. "
    "status: investigating | resolved | accepted_both | dismissed. "
    "dismissed means it was not a real conflict. "
    "resolved may name winner_id in the note."
)

_OPEN_TOOL = {
    "name": "ember_open_conflicts",
    "description": (
        "Open conflict queue for a namespace: status open or investigating. "
        "Hints from write are not in this list until ember_map_conflict."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "namespace": {"type": "string"},
            "agent_id": {"type": "string"},
            "token": {"type": "string"},
        },
        "required": [],
    },
}


def _conflict_dict(c) -> dict:
    return {
        "conflict_id": c.conflict_id,
        "namespace": c.namespace,
        "memory_a": c.memory_a,
        "memory_b": c.memory_b,
        "conflict_type": c.conflict_type.value,
        "status": c.status.value,
        "detected_by": c.detected_by,
        "resolution": c.resolution,
        "note": c.note,
    }


_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from . import server

    for tool in server.TOOLS:
        name = tool.get("name")
        if name == "ember_write":
            props = tool.get("inputSchema", {}).get("properties", {})
            if "subject" in props:
                props["subject"]["description"] = _WRITE_SUBJECT
        elif name == "ember_resolve_conflict":
            tool["description"] = _RESOLVE_DESC
            props = tool.setdefault("inputSchema", {}).setdefault("properties", {})
            props["status"] = {
                "type": "string",
                "description": "investigating | resolved | accepted_both | dismissed",
            }
            props["winner_id"] = {
                "type": "string",
                "description": "Optional. Memory id that wins when status is resolved.",
            }

    if not any(t.get("name") == "ember_open_conflicts" for t in server.TOOLS):
        idx = next((i for i, t in enumerate(server.TOOLS)
                    if t.get("name") == "ember_conflicts_for"), len(server.TOOLS))
        server.TOOLS.insert(idx + 1, _OPEN_TOOL)

    orig = server.EmberMCP._call

    def _call(self, name: str, args: dict):
        if name == "ember_write":
            result = orig(self, name, args)
            hints = getattr(self.protocol, "_last_conflict_hints", None) or []
            if not hints or not isinstance(result, dict) or result.get("isError"):
                return result
            try:
                import json
                body = json.loads(result["content"][0]["text"])
                body["possible_conflicts"] = hints
                result["content"][0]["text"] = json.dumps(body)
            except Exception:
                return result
            return result

        if name == "ember_open_conflicts":
            self._auth(args)
            ns = args.get("namespace") or self.protocol.namespace
            found = []
            for c in self.db.conflict_records(namespace=ns):
                if c.status in _OPEN_QUEUE:
                    found.append(_conflict_dict(c))
            return server._text(found)

        if name == "ember_resolve_conflict":
            agent = self._auth(args)
            raw = (args.get("status") or "resolved").lower()
            status = _RESOLVE_STATUSES.get(raw)
            if status is None:
                return server._err(
                    "status must be investigating | resolved | accepted_both | dismissed"
                )
            note = args.get("resolution") or ""
            if args.get("winner_id"):
                note = (note + f" winner={args['winner_id']}").strip()
            if raw == "dismissed" and "dismissed" not in note.lower():
                note = (note + " dismissed: not a conflict").strip()
            new_id, old_id = self.db.update_conflict_status(
                args["conflict_id"], status, note, changed_by=agent.agent_id)
            return server._text({
                "conflict_id": new_id,
                "superseded": old_id,
                "status": status.value,
            })

        if name == "ember_reflect":
            result = orig(self, name, args)
            if not isinstance(result, dict) or result.get("isError"):
                return result
            try:
                import json
                body = json.loads(result["content"][0]["text"])
                ns = args.get("namespace") or self.protocol.namespace
                body["open_conflicts"] = [
                    _conflict_dict(c)
                    for c in self.db.conflict_records(namespace=ns)
                    if c.status in _OPEN_QUEUE
                ]
                result["content"][0]["text"] = json.dumps(body)
            except Exception:
                return result
            return result

        return orig(self, name, args)

    server.EmberMCP._call = _call
    _INSTALLED = True
