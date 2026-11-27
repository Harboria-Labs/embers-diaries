"""Provider-neutral MCP tool for the same Lobby V2 operations as REST."""

from ..lobby import working
from . import server

_INSTALLED = False

_TOOL = {"name": "ember_lobby_v2",
         "description": "Authorized shared-task working memory. Actions: create_scope, grant, revoke, upload_artifact, submit, query, get, evidence, artifact, contribute, handoff, close. SSE change events use the REST stream. Pass session_id and scope_id where applicable.",
         "inputSchema": {"type": "object", "properties": {
             "action": {"type": "string"}, "session_id": {"type": "string"},
             "scope_id": {"type": "string"}, "item_id": {"type": "string"},
             "agent_id": {"type": "string"}, "token": {"type": "string"},
             "grantee": {"type": "string"}, "task": {"type": "string"},
             "namespace": {"type": "string"}, "room": {"type": "string"},
             "client_submission_id": {"type": "string"},
             "client_contribution_id": {"type": "string"},
             "share": {"type": "boolean"}, "kind": {"type": "string"},
             "claim": {"type": "string"}, "uncertainty": {"type": "string"},
             "uncertainty_note": {"type": "string"},
             "evidence_status": {"type": "string"},
             "evidence_refs": {"type": "array", "items": {"type": "object"}},
             "context": {"type": "string"}, "limit": {"type": "integer"},
             "artifact_id": {"type": "string"}, "note": {"type": "string"},
             "content": {"type": "string"}, "safe_to_share": {"type": "boolean"},
             "redacted": {"type": "boolean"},
             "client_artifact_id": {"type": "string"}},
             "required": ["action", "session_id"]}}


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    if not any(t.get("name") == "ember_lobby_v2" for t in server.TOOLS):
        server.TOOLS.append(_TOOL)
    previous = server.EmberMCP._call

    def call(self, name, args):
        if name != "ember_lobby_v2":
            return previous(self, name, args)
        args = args or {}
        try:
            actor = self._auth(args).agent_id
            db, sid, scope_id = self.db, args.get("session_id"), args.get("scope_id")
            action = args.get("action")
            if action == "create_scope":
                result = working.create_scope(db, sid, actor, task=args.get("task"),
                                              namespace=args.get("namespace"),
                                              room=args.get("room", "task"))
            elif action == "grant":
                result = working.grant(db, scope_id, sid, actor, args.get("grantee"))
            elif action == "revoke":
                result = working.revoke(db, scope_id, sid, actor, args.get("grantee"))
            elif action == "submit":
                result = working.submit(db, scope_id, sid, actor, args)
            elif action == "upload_artifact":
                result = working.upload_artifact(db, scope_id, sid, actor,
                    content=args.get("content"), safe_to_share=args.get("safe_to_share"),
                    redacted=args.get("redacted"),
                    client_artifact_id=args.get("client_artifact_id"))
            elif action == "query":
                result = {"items": working.query(db, scope_id, sid, actor,
                                                  context=args.get("context", ""),
                                                  limit=args.get("limit", 20))}
            elif action == "get":
                result = working.get(db, scope_id, sid, actor, args.get("item_id"))
            elif action == "evidence":
                item = working.get(db, scope_id, sid, actor, args.get("item_id"), evidence=True)
                result = {"evidence_refs": item["evidence_refs"] + [
                    e for c in item["contributions"] for e in c["evidence_refs"]]}
            elif action == "artifact":
                result = working.get_artifact(db, scope_id, sid, actor,
                                              args.get("artifact_id"))
            elif action == "contribute":
                result = working.contribute(db, scope_id, sid, actor,
                                            args.get("item_id"), args)
            elif action == "handoff":
                result = working.handoff(db, scope_id, sid, actor, args.get("item_id"))
            elif action == "close":
                result = working.close_scope(db, scope_id, sid, actor)
            else:
                raise working.WorkingError("unknown Lobby V2 action")
            return server._text(result)
        except (working.WorkingError, PermissionError, TypeError) as exc:
            return server._err(str(exc))

    server.EmberMCP._call = call
    _INSTALLED = True
