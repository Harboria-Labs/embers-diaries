# Ember's Diaries

**A cognitive database engine for AI memory systems.**

> Nothing is ever deleted. Nothing is ever overwritten.  
> Every state that ever existed is preserved. The past is first-class.

Ember stores agent memory as an append-only record log. Updates create a new version. Deletes are deprecations. Cognitive processes (decay, consolidation, episodes, reflection) run on top of that log; they do not invent a new forgetting law or a new retrieval algorithm.

---

## Install

```bash
pip install git+https://github.com/ticketguy/embers-diaries.git
```

With all optional features:
```bash
pip install "git+https://github.com/ticketguy/embers-diaries.git#egg=embers-diaries[all]"
```

---

## Quick Start

```python
from embers import EmberDB, EmberRecord, RecordType

# Connect (creates store if it doesn't exist)
db = EmberDB.connect("./my_store")

# Write a record
record_id = db.write(EmberRecord(
    namespace="memories",
    data={"content": "First memory", "emotion": "curious"},
    tags=["personal", "first"],
))

# Read it back
record = db.get(record_id)
print(record.data)  # {"content": "First memory", "emotion": "curious"}

# Update (creates new version — old is preserved)
new_id, old_id = db.update(record_id, {"content": "Updated memory"})

# Original still accessible
original = db.get(old_id, include_superseded=True)

# Full history
history = db.get_history(record_id)
print(len(history))  # 2

# Annotate (never modifies the original)
from embers import Annotation
db.annotate(record_id, Annotation(
    content="This memory became significant later",
    written_by="lila_emergence",
))

# Deprecate (marks as inactive — never deletes)
db.deprecate(record_id)
```

---

## LLM Integration (Memory Protocol)

The high-level interface for connecting language models to Ember:

```python
from embers import EmberDB
from embers.integration import MemoryProtocol

db = EmberDB.connect("./agent_memory")
protocol = MemoryProtocol(db)

# Store memories
protocol.remember("The user's name is Alex")
protocol.remember("Alex prefers dark mode", tags=["preference"])

# Recall relevant context
context = protocol.recall("What do I know about the user?")
# → formatted text ready for prompt injection

# Cognitive operations (adopted mechanisms; see below)
protocol.reflect()        # Check decay, find conflicts
protocol.consolidate()    # Merge related memories
episodes = protocol.segment_episodes()  # Group into episodes

# Verify facts
protocol.verify(record_id, status="verified", note="Confirmed by user")
```

---

## REST API

Start the server:
```bash
pip install "embers-diaries[api]"
EMBER_STORE=./my_store uvicorn embers.api:app --port 9200
```

Endpoints:

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Status and stats |
| POST | `/records` | Write a new record |
| GET | `/records/{id}` | Get a record |
| PUT | `/records/{id}` | Update (supersede) |
| DELETE | `/records/{id}` | Deprecate (never delete) |
| GET | `/records/{id}/history` | Version history |
| POST | `/records/{id}/annotate` | Add annotation |
| GET | `/namespaces` | List namespaces |
| GET | `/namespaces/{ns}` | Records in namespace |
| GET | `/search?q=...` | Full-text BM25 search |
| POST | `/query` | Filtered query |
| POST | `/graph/link` | Create graph edge |
| GET | `/graph/neighbors/{id}` | Graph traversal |
| POST | `/memory/remember` | Store memory (LLM) |
| POST | `/memory/recall` | Retrieve context (LLM) |
| POST | `/memory/reflect` | Run reflection cycle |
| POST | `/memory/consolidate` | Consolidate memories |
| GET | `/memory/conflicts` | Unresolved conflicts |
| GET | `/memory/stats` | Memory statistics |
| GET | `/timeline/{ns}` | Chronological view |

---

## Architecture

```
┌────────────────────────────────────────────────┐
│              REST API (FastAPI)                 │
├────────────────────────────────────────────────┤
│         LLM Integration Layer                  │
│  MemoryProtocol · ContextBuilder · Embeddings  │
├────────────────────────────────────────────────┤
│            Cognitive Layer                      │
│  Decay · Conflicts · Consolidation · Episodes  │
│  Reflection · Episodic Segmentation            │
├────────────────────────────────────────────────┤
│         Namespace & Access Control             │
│  Public · Private · Internal · Grant/Revoke    │
├────────────────────────────────────────────────┤
│              Query Engine                       │
│  Document · Graph · Timeline · Vector · BM25   │
├────────────────────────────────────────────────┤
│              Index Layer                        │
│  Master · Graph · Timeline · Vector · FullText │
├────────────────────────────────────────────────┤
│         Core Record Engine                     │
│  Append-only · WAL · Supersession · Immutable  │
└────────────────────────────────────────────────┘
```

---

## Core Principles

| Traditional DB | Ember's Diaries |
|---|---|
| INSERT → row created | WRITE → record created |
| UPDATE → row mutated | WRITE → new record, old marked superseded |
| DELETE → row destroyed | DEPRECATE → original preserved, marked inactive |

These write rules are event-sourcing / bitemporal practice applied to agent memory. Ember did not invent append-only logs.

---

## Record Types

| Type | Use case |
|---|---|
| `DOCUMENT` | Structured or semi-structured data |
| `NODE` | Graph vertices — entities, concepts |
| `EDGE` | Graph connections between nodes |
| `TIMESERIES` | Sequential data indexed by time |
| `VECTOR` | Embedding records for semantic search |
| `RAW` | Binary, blobs, unstructured data |
| `EVIDENCE` | Hashed observation supporting a claim |
| `PROPOSAL` | Discovery awaiting validation |
| `CONFLICT` | Mapped contradiction between memories |
| `SESSION` | Bounded period of agent activity |
| `FAILURE` | Failed approach, kept so others do not repeat it |

---

## What Ember designed vs what it adopted

### Designed here (the epistemic path)

These are Ember's own product rules. They are implemented as first-class types, not as a published mathematical theory.

| Feature | What Ember does |
|---|---|
| **Proposal before memory** | A discovery is stored as a `PROPOSAL` and only becomes durable memory if it passes promotion policy. |
| **Promotion ≠ truth** | Admission means the proposal met store criteria. The memory can still be `PROVISIONAL`, `VERIFIED`, or `DISPUTED`. |
| **Hashed evidence** | Support is a separate object with source type (observed, inferred, reported, …), so a bare assertion is distinguishable from a grounded claim. |
| **Failures first-class** | A failed approach is a lookupable record (`FAILED` / `CAUSE` / `EVIDENCE`), not discarded noise. |
| **Rejected proposals kept** | Rejection does not delete the proposal or turn it into a committed memory. |
| **Provenance in the hash** | Who / session / why / derived-from are sealed into the record identity. |

### Adopted mechanisms (not Ember discoveries)

| Feature | What Ember implements | Source of the idea |
|---|---|---|
| **Decay** | Read-time confidence using an exponential curve; access can slow the rate. Records are not mutated. | Ebbinghaus / SuperMemo-style retention. Common in agent-memory systems. |
| **Consolidation** | New long-term records that point at sources (sensory → short-term → long-term). Grouping is tag/time heuristic. | Atkinson-Shiffrin multi-store model. |
| **Episodic segmentation** | Groups records by time gap, tag shift, namespace change, and tag-rarity surprise. | Inspired by EM-LLM (Fountas et al.): surprise-based event boundaries. Ember is a simpler engineering variant, not that paper's attention-graph method. |
| **Reflection** | Writes annotations when decay or unresolved conflicts fire. | Standard metacognitive / Reflexion-style loop. |
| **Conflict scan** | Field-value and high-similarity/different-data checks; mapped conflicts can also be stored as `CONFLICT` records. | Ordinary consistency checking. |
| **Recall** | Hybrid vector similarity + BM25. | Standard retrieval, not a new search method. |

---

## Status

| Component | Status |
|-----------|--------|
| Core record engine (write/read/supersession/WAL) | Implemented |
| Index layer (graph/timeline/vector/full-text) | Implemented |
| Query engine (document/graph/similarity/BM25) | Implemented |
| Namespaces & access control | Implemented |
| Cognitive layer (decay/conflicts/consolidation/episodes/reflection) | Implemented (heuristic engines) |
| Epistemic types (evidence/proposal/failure/promotion/sessions) | Implemented |
| Python SDK (MemoryProtocol/ContextBuilder) | Implemented |
| REST API (FastAPI server) | Implemented |
| PyPI package | Next |

Run the suite for the current count:

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

---

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Write model | Append-only | History is the asset |
| Deletion | Deprecation only | Nothing is ever truly gone |
| Data model | Multi-model native | Cognitive systems need graph, time, and vector together |
| Crash safety | Write-ahead log | Atomic writes, no partial records |
| Serialization | MessagePack | Compact binary, fast, language-agnostic |
| Cognitive math | Adopted models | Decay and episode boundaries are known methods applied to Ember records |

---

## License

MIT

Built by 0xticketguy / Harboria Labs.
