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
        "name": "ember_update",
        "description": (
            "Create a new immutable version of a memory using expected_hash "
            "as an optimistic-concurrency precondition. A stale hash returns "
            "a structured storage conflict and writes nothing."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "record_id": {"type": "string"},
                "data": {"type": "object"},
                "expected_hash": {"type": "string"},
                "session_id": {"type": "string"},
                "creation_reason": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["record_id", "data", "expected_hash"],
        },
    },
    {
        "name": "ember_query",
        "description": (
            "Query records by namespace, exact field filters, and tags. "
            "Returns complete records; pass session_id to scope the query to "
            "records attributed to one session."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "filters": {
                    "type": "object",
                    "description": (
                        "Exact EmberRecord or data-field matches. Also accepts "
                        "record_type, confidence_min, confidence_max, and "
                        "session_id."
                    ),
                },
                "tags": {"type": "array", "items": {"type": "string"}},
                "session_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1},
                "include_deprecated": {"type": "boolean"},
                "include_superseded": {"type": "boolean"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": [],
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
                "evidence": {"type": "array","items":{"type":"object","additionalProperties":False,"properties":{
                    "source":{"type":"string"},
                    "source_type":{"type":"string"},
                    "reference":{"type":"string"},
                    "description":{"type":"string"},
                    "origin":{"type":"string","description":"Underlying source/origin identity. If omitted, Ember records origin=unknown."},
                    "origin_confidence":{"type":"string","enum":["UNKNOWN","AGENT_DECLARED"],"description":"Identity confidence only; never evidence strength. SYSTEM_CONFIRMED is reserved for system-captured provenance."},
                    "event_id":{"type":"string","description":"Optional observation/event identity used for dependency detection."},
                    "derived_from":{"type":"array","items":{"type":"string"},"uniqueItems":True,"description":"Evidence identities this item derives from."}
                },"required":["source"]}},
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
                        "criteria to become durable memory, NOT that it is true. "
                        "Epistemic state is maintained separately by ELLA."),
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
                "origin": {"type": "string","description": "Underlying source/origin identity. If omitted, Ember records origin=unknown."},
                "origin_confidence": {"type": "string","enum": ["UNKNOWN","AGENT_DECLARED"],"description": "Identity confidence only; never evidence strength. SYSTEM_CONFIRMED is reserved for system-captured provenance."},
                "event_id": {"type": "string","description": "Optional observation/event identity used for dependency detection."},
                "derived_from": {"type": "array","items": {"type": "string"},"uniqueItems": True},
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
        "description": ("Map a SEMANTIC contradiction between two "
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
                "conflict_type": {"type": "string", "enum": ["semantic"],
                    "description": "semantic; storage races use ember_update"},
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

# Contract 01 metadata is separate from query text and room filters.
for _tool in TOOLS:
    if _tool["name"] in ("ember_write", "ember_recall"):
        _tool["inputSchema"]["properties"]["primary_context"] = {
            "type": ["string", "null"],
            "description": "Optional exact agent-supplied primary context. No inference, merging or context filtering."}
    if _tool["name"] == "ember_recall":
        _tool["inputSchema"]["properties"]["inspect_context"] = {
            "type": "boolean", "description": "Return query/context, candidate/result IDs with their stored contexts, and memories. Use true to inspect unset context."}


TOOLS.append({
    "name": "ember_orient",
    "description": "Read-only Contract 02: find likely existing subjects, contexts and bounded memory previews from rough clues. Returns lexical/relationship signals, applied limits and IDs. The agent chooses the working context; no context merging, rewriting or learning.",
    "inputSchema": {"type": "object", "properties": {
        "clues": {"type": "string", "description": "Rough keywords/fragments, up to 2048 UTF-8 bytes; not a primary context."},
        "namespace": {"type": "string"},
        "hints": {"type": "object", "additionalProperties": False, "properties": {
            "subject": {"type": ["string", "null"]}, "primary_context": {"type": ["string", "null"]}}},
        "limits": {"type": "object", "additionalProperties": False, "properties": {
            "subjects": {"type": "integer", "minimum": 1, "maximum": 20},
            "contexts": {"type": "integer", "minimum": 1, "maximum": 40},
            "memories_per_context": {"type": "integer", "minimum": 1, "maximum": 10},
            "records": {"type": "integer", "minimum": 1, "maximum": 100},
            "candidates": {"type": "integer", "minimum": 1, "maximum": 500},
            "relationships": {"type": "integer", "minimum": 1, "maximum": 100},
            "preview_chars": {"type": "integer", "minimum": 1, "maximum": 1000},
            "response_bytes": {"type": "integer", "minimum": 4096, "maximum": 131072}}},
        "signals": {"type": "array", "uniqueItems": True, "items": {"type": "string", "enum": ["subject", "primary_context", "content", "tags", "relations"]}},
        "agent_id": {"type": "string"}, "token": {"type": "string"}, "session_id": {"type": "string"}},
        "required": ["clues"]}})


TOOLS.extend([
 {"name":"ember_usefulness_update", "description":"Evidence-derived usefulness. Submit reports or authorized resolve/merge/split/configure decisions. Never updates heat or truth. request_id is idempotent; decisions require current expected_revision.",
  "inputSchema":{"type":"object","properties":{
   "namespace":{"type":"string"},"action":{"type":"string","enum":["report","resolve","merge","split","configure"]},
   "payload":{"type":"object","additionalProperties":False,
    "description":"report: target/context/feedback_type, optional identity/session/query ID/note/experience ID. resolve: experience_id/status/feedback_type/reason. merge: experience_ids/reason. split: experience_id/partitions/reason. configure: policy/reason. Decisions require expected_revision from ember_usefulness_state.",
    "properties":{
     "target":{"type":"object","additionalProperties":False,"required":["kind","memory_ids"],"properties":{
      "kind":{"type":"string","enum":["memory","pair","group"]},
      "memory_ids":{"type":"array","items":{"type":"string"},"minItems":1,"maxItems":32,"uniqueItems":True},
      "relation":{"type":"string","enum":["explains","requires","warns_about","alternative"]}}},
     "context":{"type":["string","null"],"description":"Exact agent-supplied context; null is explicitly unset. No transfer to similar contexts."},
     "feedback_type":{"type":["string","null"],"enum":["CONTRIBUTED","IRRELEVANT","MISLEADING","UNUSED","PAIR_HELPED","PAIR_IRRELEVANT","GROUP_SUCCESS","GROUP_FAILURE",None]},
     "session_id":{"type":"string"},"query_request_id":{"type":"string"},"note":{"type":"string"},
     "identity":{"type":"object","additionalProperties":False,"required":["value","source","provenance"],"properties":{
      "value":{"type":"string"},"source":{"type":"string"},"provenance":{"type":"string"}}},
     "identity_verified":{"type":"boolean","description":"Only the configured decision agent may attest identity. Ordinary IDs are unverified claims."},
     "experience_id":{"type":"string"},"experience_ids":{"type":"array","items":{"type":"string"}},
     "partitions":{"type":"array","items":{"type":"array","items":{"type":"string"}}},
     "status":{"type":"string","enum":["accepted","unresolved"]},"reason":{"type":"string"},
     "policy":{"type":"object","description":"Configurable kappa_u,u0,epsilon,u_max,kappa_w,w0; severity {contributed,irrelevant,misleading}; cluster {session_window,time_window} in seconds. Audited recomputation, no heat/truth coupling."}}},
   "request_id":{"type":"string","description":"Unique submission identity; identical retries are idempotent."},"expected_revision":{"type":"integer"},
   "agent_id":{"type":"string"},"token":{"type":"string"},"session_id":{"type":"string"}},
   "required":["namespace","action","payload","request_id"]}},
 {"name":"ember_usefulness_state", "description":"Read-only bounded U/W, evidence, group outcomes and chronological replay snapshot for an authorized namespace. Real-time HTTP observation is available via authenticated GET /v1/visualizer-stream/{namespace}, resuming with Last-Event-ID; this MCP tool remains the bounded recovery snapshot.",
  "inputSchema":{"type":"object","properties":{
   "request_id":{"type":"string"},"filter_session_id":{"type":"string"},
   "namespace":{"type":"string"},"after":{"type":"integer","minimum":0},"limit":{"type":"integer","minimum":1,"maximum":200},
   "agent_id":{"type":"string"},"token":{"type":"string"},"session_id":{"type":"string"}},"required":["namespace"]}}
])


TOOLS.append({"name":"ember_visualizer_access",
 "description":"Create a temporary read-only visualizer code/link for the user without sharing agent credentials, or revoke a previously issued view. Grants authorize exactly one namespace, expire in 15 minutes by default, and cannot call mutation tools. Return code and viewer_url/viewer_path to the user. Supply server_url (the existing server origin) for a complete link, or configure EMBER_PUBLIC_URL.",
 "inputSchema":{"type":"object","properties":{
  "action":{"type":"string","enum":["create","revoke"],"default":"create"},
  "namespace":{"type":"string"},"ttl_seconds":{"type":"integer","minimum":60,"maximum":3600,"default":900},
  "server_url":{"type":"string"},"grant_id":{"type":"string"},
  "agent_id":{"type":"string"},"token":{"type":"string"},"session_id":{"type":"string"}}}})


TOOLS.append({"name":"ember_visualize",
 "description":"Create a read-only Research Observer link following MY observable Ember activity across namespaces and sessions. No namespace required. Self-only delegation; cannot observe another agent. Does not invoke retrieval or learning. Return visualization_url/viewer_path and code to the intended human. Scope defaults to future activity in namespaces I can currently read, spanning sessions. Namespace View remains available separately through ember_visualizer_access.",
 "inputSchema":{"type":"object","properties":{
  "action":{"type":"string","enum":["create","revoke"],"default":"create"},
  "observer_target":{"type":"string","description":"If supplied, must equal authenticated caller."},
  "observer_id":{"type":"string"},"server_url":{"type":"string"},
  "ttl_seconds":{"type":"integer","minimum":60,"maximum":86400,"default":3600},
  "span_sessions":{"type":"boolean","default":True},"namespaces":{"type":"array","items":{"type":"string"},"maxItems":128},
  "agent_id":{"type":"string"},"token":{"type":"string"},"session_id":{"type":"string"}}}})

# Consolidated Rust-authoritative model, separate from explicit historical replay.
for _name, _desc, _props, _required in [
    ('ember_research_recall','Consolidated FUR U → query modulation → bounded activation. Explicit agent direct scores and model time. W is not used for expansion.',
     {'query_id':{'type':'string'},'direct_scores':{'type':'object','additionalProperties':{'type':'number','minimum':0,'maximum':1}},'elapsed':{'type':'number','minimum':0},'context':{'type':['string','null']},'format':{'type':'string','enum':['structured','text','messages']}},['query_id','direct_scores','elapsed']),
    ('ember_research_settings','Read versioned research settings and Rust-validated recovery guarantees.',{},[]),
    ('ember_research_configure','Authorized research configuration change; Rust validates and reprojects FUR, preserving existing activation. expected_revision is the journal revision.',
     {'config':{'type':'object'},'policy':{'type':'object'},'request_id':{'type':'string'},'expected_revision':{'type':'integer','minimum':0},'reason':{'type':'string'}},['config','policy','request_id','expected_revision','reason'])]:
    TOOLS.append({'name':_name,'description':_desc,'inputSchema':{'type':'object','properties':{'namespace':{'type':'string'},'agent_id':{'type':'string'},'token':{'type':'string'},'session_id':{'type':'string'},**_props},'required':['namespace',*_required]}})

TOOLS.append({"name":"ember_pair_relationship",
 "description":"Store a directional typed relationship with explicit context for Pairing Matrix V1. Does not update W. Repeating the same relationship reuses its identity. Use ember_usefulness_update report with a pair target for explicit route feedback.",
 "inputSchema":{"type":"object","properties":{
 "source":{"type":"string"},"target":{"type":"string"},
 "relation":{"type":"string","enum":["explains","requires","warns_about","alternative"]},
 "primary_context":{"type":["string","null"]},
 "agent_id":{"type":"string"},"token":{"type":"string"},"session_id":{"type":"string"}},
 "required":["source","target","relation","primary_context"]}})

for _name,_props,_required in [
    ('ember_epistemic_state',{'memory_id':{'type':'string'}},['memory_id']),
    ('ember_epistemic_feedback',{'action':{'type':'string','enum':['report','revise','confirm','withdraw','resolve','invalidate_evidence','correct_evidence','merge','split','carry_forward','configure']},'payload':{'type':'object'},'request_id':{'type':'string'},'expected_revision':{'type':'integer','minimum':0}},['action','payload','request_id','expected_revision'])]:
    TOOLS.append({'name':_name,'description':'ELLA V1 exact-version evidence assessment; usefulness and retrieval remain separate. Feedback requires exact target content hash, evidence record ID, SUPPORTS/OPPOSES, WEAK/MEDIUM/STRONG and a concise assessment_note. Numeric likelihoods are forbidden.', 'inputSchema':{'type':'object','properties':{'namespace':{'type':'string'},'agent_id':{'type':'string'},'token':{'type':'string'},'session_id':{'type':'string'},**_props},'required':['namespace',*_required]}})
