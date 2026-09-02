# 🔥 Ember's Diaries

**A cognitive database engine for AI memory systems.**

> Nothing is ever deleted. Nothing is ever overwritten.  
> Every state that ever existed is preserved. The past is first-class.

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

# Cognitive operations
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
│              REST API (FastAPI)                 │  ← Phase 5
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

---

## Cognitive Features

| Feature | What it does |
|---|---|
| **Decay** | Ebbinghaus-curve confidence decay over time |
| **Consolidation** | Sensory → short-term → long-term memory promotion |
| **Conflict Detection** | Finds contradictions between memories |
| **Episodic Segmentation** | Groups memories into conversation episodes |
| **Reflection** | Meta-cognitive analysis of memory health |
| **Context Building** | Formats memories for LLM prompt injection |

---

## Status

| Component | Status |
|-----------|--------|
| Core record engine (write/read/supersession/WAL) | ✅ Complete |
| Index layer (graph/timeline/vector/full-text) | ✅ Complete |
| Query engine (document/graph/similarity/BM25) | ✅ Complete |
| Namespaces & access control | ✅ Complete |
| Cognitive layer (decay/conflicts/consolidation) | ✅ Complete |
| Python SDK (MemoryProtocol/ContextBuilder) | ✅ Complete |
| REST API (FastAPI server) | ✅ Complete |
| PyPI package | 🔜 Next |

**114 tests passing.**

---

## Running Tests

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

---

## License

MIT

Built by 0xticketguy / Harboria Labs.
