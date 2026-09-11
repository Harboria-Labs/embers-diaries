"""MCP tool catalog. Imported by server.py so tools/list matches runtime."""

TOOLS = [
    {
        "name": "ember_register",
        "description": "Register this agent. Returns agent_id and token. Store both.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "provider": {"type": "string"},
                "model": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "ember_write",
        "description": "Write a durable memory attributed to the authenticated agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "subject": {"type": "string",
                    "description": ("Optional. Names the entity this memory "
                        "is about. If another row shares the subject and a "
                        "claim field differs, write returns possible_conflicts "
                        "as a hint. Ember does not map a conflict. Use "
                        "ember_map_conflict only if you decide it is one. "
                        "Different wording in content is not a conflict.")},
                "namespace": {"type": "string"},
                "session_id": {"type": "string"},
                "creation_reason": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "ember_read",
        "description": "Read a record by id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "record_id": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["record_id"],
        },
    },
    {
        "name": "ember_search",
        "description": "Full-text search over memories.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "namespace": {"type": "string"},
                "top_k": {"type": "integer"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "ember_recall",
        "description": "Retrieve relevant memories for a query.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "namespace": {"type": "string"},
                "top_k": {"type": "integer"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "ember_get_history",
        "description": "Version history for a record.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "record_id": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["record_id"],
        },
    },
    {
        "name": "ember_get_graph",
        "description": "Graph neighbors of a record.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "record_id": {"type": "string"},
                "depth": {"type": "integer"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["record_id"],
        },
    },
    {
        "name": "ember_get_session",
        "description": "Load a session by id.",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
    },
    {
        "name": "ember_start_session",
        "description": "Open a session for the authenticated agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "namespace": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
        },
    },
    {
        "name": "ember_propose_memory",
        "description": "Submit a memory proposal (not yet durable memory).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "discovery": {},
                "reason": {"type": "string"},
                "confidence": {"type": "number"},
                "namespace": {"type": "string"},
                "session_id": {"type": "string"},
                "evidence": {"type": "array"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["discovery"],
        },
    },
    {
        "name": "ember_submit",
        "description": ("Route a pending proposal through the Promotion Engine. "
                        "The engine decides (per configured mode + policy) whether "
                        "it enters durable memory. On a hold nothing is written and "
                        "the proposal stays pending."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "proposal_id": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["proposal_id"],
        },
    },
    {
        "name": "ember_promotion_route",
        "description": ("Dry run: what would the Promotion Engine decide for this "
                        "proposal? Writes nothing."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "proposal_id": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["proposal_id"],
        },
    },
    {
        "name": "ember_promote",
        "description": ("Explicitly promote a pending proposal into durable memory "
                        "(an authenticated caller's own decision, recorded as "
                        "promotion_method=human). Promotion means it met the "
                        "criteria to become durable memory, NOT that it is true — "
                        "the memory carries its own status."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "proposal_id": {"type": "string"},
                "status": {"type": "string",
                            "description": "verified / provisional / disputed"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["proposal_id"],
        },
    },
    {
        "name": "ember_reject",
        "description": ("Reject a pending proposal. Append-only: it stays "
                        "permanently queryable as rejected and never becomes a "
                        "memory."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "proposal_id": {"type": "string"},
                "reason": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["proposal_id"],
        },
    },
    {
        "name": "ember_list_proposals",
        "description": ("Proposals in a namespace, optionally filtered by status "
                        "(pending / promoted / rejected) — find what awaits a "
                        "promotion decision."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "status": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["namespace"],
        },
    },
    {
        "name": "ember_attach_evidence",
        "description": ("Attach independent evidence to an EXISTING durable "
                        "memory. Append-only — the memory is not modified, so its "
                        "hash is untouched and its confirmation trail only grows."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
                "source": {"type": "string"},
                "source_type": {"type": "string"},
                "reference": {"type": "string"},
                "description": {"type": "string"},
                "session_id": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["memory_id", "source"],
        },
    },
    {
        "name": "ember_evidence_for",
        "description": ("Every evidence record supporting a memory. An empty list "
                        "means the memory rests on a bare assertion, not evidence."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["memory_id"],
        },
    },
    {
        "name": "ember_report_failure",
        "description": "Record a failed approach so other agents can skip it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "approach": {"type": "string"},
                "failed": {"type": "string"},
                "cause": {"type": "string"},
                "namespace": {"type": "string"},
                "session_id": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["approach"],
        },
    },
    {
        "name": "ember_map_conflict",
        "description": ("Map a SEMANTIC (or STORAGE) contradiction between two "
                        "existing memories (spec §7). Neither memory is modified "
                        "or destroyed — this records a CONFLICT record (status "
                        "OPEN) and draws a symmetric contradicts edge between "
                        "them, so the contradiction is visible both as a "
                        "queryable object and via graph traversal. Idempotent: "
                        "mapping the same live pair again returns the existing "
                        "conflict id rather than duplicating it."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_a": {"type": "string"},
                "memory_b": {"type": "string"},
                "conflict_type": {"type": "string",
                    "description": "semantic (default) or storage"},
                "note": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["memory_a", "memory_b"],
        },
    },
    {
        "name": "ember_conflicts_for",
        "description": ("Every mapped Conflict involving a given memory, on "
                        "either side. By default only live (not resolved/"
                        "superseded) conflicts; pass include_closed for the "
                        "full triage history."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
                "include_closed": {"type": "boolean"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["memory_id"],
        },
    },
    {
        "name": "ember_resolve_conflict",
        "description": ("Record a decision on a mapped conflict. Neither "
                        "memory is deleted. status: investigating | resolved | "
                        "accepted_both | dismissed. dismissed means it was "
                        "not a real conflict. resolved may name winner_id."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "conflict_id": {"type": "string"},
                "resolution": {"type": "string"},
                "status": {"type": "string",
                    "description": "investigating | resolved | accepted_both | dismissed"},
                "winner_id": {"type": "string",
                    "description": "Optional. Memory id that wins when status is resolved."},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["conflict_id", "resolution"],
        },
    },
    {
        "name": "ember_open_conflicts",
        "description": ("Open conflict queue for a namespace: status open or "
                        "investigating. Hints from write are not in this list "
                        "until ember_map_conflict."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": [],
        },
    },
    {
        "name": "ember_reflect",
        "description": ("Run a reflection cycle over a namespace: examines "
                        "memories for confidence decay and any custom "
                        "reflection triggers, and PERSISTS the resulting "
                        "reflective annotations (db.annotate) -- it does not "
                        "modify or create memories, only comments on them. "
                        "Nothing calls this automatically; there is no "
                        "scheduler. Run it yourself periodically, or have an "
                        "agent call it at the end of a session."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "limit": {"type": "integer"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": [],
        },
    },
    {
        "name": "ember_consolidate",
        "description": ("Run memory consolidation over a namespace: groups "
                        "memories by shared tags or temporal proximity and "
                        "writes a new, higher-confidence consolidated "
                        "record linking back to every source (sources are "
                        "never deprecated or deleted). The consolidated "
                        "record lands in the SAME namespace it read from, "
                        "so it's findable via a plain ember_recall "
                        "afterward. Nothing calls this automatically -- run "
                        "it yourself periodically."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": [],
        },
    },
    {
        "name": "ember_segment_episodes",
        "description": ("Group a namespace's memories into episodes using "
                        "temporal gaps, tag-overlap shifts, and a surprise "
                        "score -- returns the groupings directly; nothing "
                        "is written to the store (episodes aren't persisted "
                        "as their own record type). Purely a read-side "
                        "computation for the caller to use or discard."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": [],
        },
    },
]
