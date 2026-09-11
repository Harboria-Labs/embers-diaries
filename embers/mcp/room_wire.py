"""Attach room + kind to MCP write/recall without rewriting server.py.

Kind ≠ room. Missing labels store as unscoped. Recall does not guess.
"""

from . import server

_INSTALLED = False

_WRITE_TYPE = {
    "type": "string",
    "description": (
        "Kind of memory: unscoped | raw | skill | failure | "
        "episodic | connective | reflective. Kind is not room. "
        "If omitted or unknown, stored as unscoped."
    ),
}
_WRITE_ROOM = {
    "type": "string",
    "description": (
        "Where it belongs: personal | project | task | unscoped. "
        "Set this on every write. If omitted or unknown, stored "
        "as unscoped. Recall will not invent a room later."
    ),
}
_RECALL_ROOM = {
    "type": "string",
    "description": (
        "Optional. Keep only memories whose stored room matches "
        "(personal | project | task | unscoped). Unscoped rows "
        "stay unscoped; recall does not guess personal/project."
    ),
}


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _patch_schema()
    _patch_call()
    _INSTALLED = True


def _patch_schema() -> None:
    for tool in server.TOOLS:
        name = tool.get("name")
        props = tool.get("inputSchema", {}).get("properties", {})
        if name == "ember_write":
            tool["description"] = (
                "Write a durable memory. Pass memory_type (kind) and room "
                "(personal / project / task). If either is omitted, it is "
                "stored as unscoped. Ember does not guess."
            )
            props["memory_type"] = _WRITE_TYPE
            props["room"] = _WRITE_ROOM
        elif name == "ember_recall":
            props["room"] = _RECALL_ROOM


def _patch_call() -> None:
    orig = server.EmberMCP._call

    def _call(self, name: str, args: dict):
        if name == "ember_write":
            remember = self.protocol.remember

            def wrapped(content, *a, **kw):
                kw.setdefault("memory_type", args.get("memory_type", "unscoped"))
                kw.setdefault("room", args.get("room", "unscoped"))
                return remember(content, *a, **kw)

            self.protocol.remember = wrapped
            try:
                return orig(self, name, args)
            finally:
                self.protocol.remember = remember

        if name == "ember_recall":
            recall = self.protocol.recall

            def wrapped(query, *a, **kw):
                if "room" not in kw:
                    kw["room"] = args.get("room")
                return recall(query, *a, **kw)

            self.protocol.recall = wrapped
            try:
                return orig(self, name, args)
            finally:
                self.protocol.recall = recall

        return orig(self, name, args)

    server.EmberMCP._call = _call
