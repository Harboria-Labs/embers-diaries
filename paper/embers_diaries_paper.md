# Ember's Diaries: An Immutable Cognitive Database Engine for Grounded AI Memory

**Authors:** 0xticketguy (Harboria Labs)
**Repository:** https://github.com/Harboria-Labs/embers-diaries
**Version:** 1.0

> **Harboria Labs Research Stack**
> This paper is Layer 1 of a four-part research program.
> Layer 1 — **Ember's Diaries** *(this paper)*: cognitive memory specification
> Layer 2 — Memory Fabric: neural weight-space implementation
> Layer 3 — Fig Engine: training infrastructure
> Layer 4 — CogMem Benchmark: evaluation

---

## Abstract

Large Language Models lack persistent, structured memory that preserves the full history of what they have learned, believed, and revised. Existing memory-augmented LLM approaches either store memory externally with no lifecycle management (RAG), wire memory into latent space with destructive updates (MemoryLLM), or treat memory as a systems engineering problem without cognitive structure (MemOS). We present Ember's Diaries, an open-source cognitive database engine that provides a fundamentally different foundation: an *append-only, immutable* record store where no memory is ever deleted or overwritten, combined with cognitive processing layers inspired by human episodic memory, Ebbinghaus forgetting curves, and metacognitive reflection.

Ember's Diaries serves a dual function. As a standalone system, it is a queryable database engine for AI memory management. As a research artifact, it is the **cognitive specification** (Layer 1) of the Harboria Labs memory stack — a behavioral protocol that defines what memory operations should do, independent of how they are implemented. Just as TCP specifies networking behavior without defining hardware, Ember's Diaries specifies memory behavior without assuming a particular implementation. The companion Memory Fabric paper describes one neural implementation of this specification.

Every state that ever existed is preserved and queryable. Updates create new records linked to their predecessors. Beliefs carry epistemic status (verified, hypothesis, contested). Confidence decays naturally over time but is reinforced by access. Conflicts between memories are detected and tracked rather than silently resolved. We implement a full-stack architecture spanning physical storage, multi-model indexing (graph, timeline, vector, full-text), a cognitive engine (episodic segmentation, memory consolidation, conflict detection, reflective annotation), and an LLM integration protocol. Our implementation passes 101 tests across all layers and is available as a pip-installable Python package.

---

## 1. Introduction

The fundamental limitation of current Large Language Models is not the absence of knowledge, but the absence of *memory*. An LLM can generate text about any topic in its training data, but it cannot remember what it generated yesterday, track how its beliefs have changed, or distinguish between verified facts and uncertain hypotheses. This limitation has driven significant research into memory-augmented LLMs, but existing approaches share a common flaw: they treat memory as mutable state.

MemoryLLM [Wang et al., 2024] maintains a pool of memory tokens per transformer layer, updated by dropping old tokens and inserting new ones. M+ [Wang et al., 2025] extends this with a retrieval mechanism but still overwrites the latent memory pool. MEGa [2025] stores memories as gated LoRA adapters that get overwritten during continual learning. Even MemOS [2025], which proposes a full memory operating system, supports mutation operations on its MemCubes.

The consequence of mutable memory is *hallucination accumulation*. The HaluMem benchmark [2024] demonstrated that memory systems exhibit fabrication, errors, conflicts, and omissions that compound over time — precisely because updating a memory destroys the evidence of what it previously was. When a memory system silently resolves a conflict by overwriting one version with another, it eliminates the very information needed to detect and prevent future hallucinations.

We propose a different principle: **nothing is ever deleted; nothing is ever overwritten.** Ember's Diaries is a cognitive database engine where every write is permanent, every update creates a new record linked to its predecessor, and the full history of any value is always queryable. This is not append-only logging — it is a complete database system with multi-model querying, cognitive processing, and LLM integration, built on immutable foundations.

The key insight is that for cognitive systems, *what something was* is as important as *what it is now*. A belief that changed, a memory that was reinterpreted, a fact that was corrected — these transitions carry meaning. They are the substrate of learning. Destroying them destroys the system's ability to learn from its own epistemic history.

### 1.1 Position in the Harboria Labs Research Stack

Ember's Diaries occupies Layer 1 of a four-part research agenda:

| Layer | Component | Role |
|---|---|---|
| 1 | **Ember's Diaries** *(this paper)* | Define what memory *should* do |
| 2 | Memory Fabric | Implement those behaviors in neural weights |
| 3 | Fig Engine | Make continuous training feasible on commodity hardware |
| 4 | CogMem Benchmark | Measure whether any implementation actually works |

Ember's Diaries makes no assumptions about neural networks, weights, or adapters. It defines a behavioral protocol: eight memory principles that any implementation must approximate. Memory Fabric is one such implementation — a neural architecture that encodes these behaviors into LoRA adapters organized by memory namespace. CogMem Benchmark evaluates implementations against Ember's principles independently of how they are built.

### 1.2 Contributions

1. **Immutable cognitive database architecture** — A complete database engine where records are never mutated, with supersession chains preserving full version history and deprecation replacing deletion.

2. **Epistemic status as a first-class field** — Every record carries a verification status (verified, hypothesis, contested, deprecated) and confidence score with natural decay, enabling the LLM to know *how certain* a memory is, not just *what* it says.

3. **Cognitive processing engine** — Episodic segmentation inspired by EM-LLM's Bayesian surprise, Ebbinghaus-curve confidence decay computed at read time, memory consolidation following the Atkinson-Shiffrin model, automated conflict detection, and metacognitive reflection that generates annotations on existing memories.

4. **LLM Memory Protocol** — A unified interface (`remember`, `recall`, `reflect`, `verify`, `forget`) that any language model can use, with automatic embedding generation, context formatting, and provenance tracking.

5. **Multi-model index layer** — Native graph traversal, timeline queries with binary search, BM25 full-text search, cosine similarity vector search, and a master index — all operating over the same immutable record store.

6. **Behavioral specification for Memory Fabric** — Eight cognitive principles (append-only history, supersession, confidence decay, episodic organization, conflict preservation, consolidation, reflection, provenance) that define what the neural implementation must approximate, independently of implementation mechanism.

---

## 2. Related Work

### 2.1 Memory Wired Into Model Parameters

**MemoryLLM** [Wang et al., 2024] introduces a memory pool of hidden vectors within each transformer layer. During self-update, K tokens are extracted from the pool, processed alongside new input, and the output replaces K randomly dropped tokens. Retention degrades beyond 20K tokens due to destructive updates.

**M+** [Wang et al., 2025] extends MemoryLLM with a co-trained retriever accessing a long-term memory bank of compressed hidden states, extending retention to 160K tokens. The short-term memory pool still overwrites.

**Memory³** [Yang et al., 2024] externalizes specific knowledge as sparse attention key-value pairs stored on disk. Lower hallucination on TruthfulQA results from explicit memories corresponding directly to reference texts, but retrieved KV pairs are static snapshots with no version history.

**MEGa** [2025] stores each memory as a gated low-rank LoRA adapter. Near-perfect recall is achieved, but adapter weights are still overwritten during fine-tuning on new memories.

### 2.2 Human-Like Memory Architectures

**EM-LLM** [Fountas et al., 2024] segments token sequences into coherent episodic events using Bayesian surprise and graph-theoretic boundary refinement. Strong correlations with human event boundaries suggest genuine cognitive structure. EM-LLM operates on KV caches and does not persist memories across sessions.

**LightMem** [2025] implements the Atkinson-Shiffrin model with sensory → short-term → long-term memory stages and sleep-time consolidation. 7.7% accuracy improvements with 106× token reduction. Ember's Diaries consolidation engine draws directly from this work.

**Atlas** [Behrouz et al., 2025] introduces a long-term memory module with super-linear capacity, achieving 80%+ accuracy at 10M context length on BABILong.

### 2.3 Memory Systems and Hallucination

**HaluMem** [2024] is the first benchmark evaluating memory hallucinations at the operation level (extraction, updating, question answering). The key finding is that hallucinations accumulate during extraction and updating stages, then propagate to downstream generation. This directly motivates Ember's Diaries' append-only design: by never overwriting a memory, we eliminate the primary source of hallucination accumulation.

### 2.4 Memory Operating Systems

**MemOS** [2025] proposes a memory operating system with MemCubes as universal encapsulation units. It unifies plaintext, activation-based, and parameter-level memories. Ember's Diaries differs philosophically: MemOS supports CRUD operations on MemCubes, while Ember's Diaries enforces strict immutability.

---

## 3. Design Principles

Ember's Diaries is built on eight cognitive memory principles. These principles constitute the behavioral specification that the companion Memory Fabric implementation approximates in neural weights.

| Principle | Definition | Why It Matters |
|---|---|---|
| **Append-only history** | New writes never modify existing records | Prevents hallucination accumulation; full history always queryable |
| **Supersession** | Updates create new records linked to predecessors | State transitions carry meaning; rollback is always possible |
| **Confidence decay** | Memory strength decays as a function of time and access | Implements Ebbinghaus forgetting curve without metadata overhead |
| **Episodic organization** | Memories are segmented into coherent episodes | Mirrors human event cognition; enables temporal retrieval |
| **Conflict preservation** | Contradictions are detected and tracked, never silently resolved | Prevents hallucination from averaged conflicting facts |
| **Consolidation** | Frequently accessed memories migrate to stable long-term storage | Implements Atkinson-Shiffrin model of memory strengthening |
| **Reflection** | System annotates its own memories with metacognitive commentary | Enables epistemic self-awareness |
| **Provenance** | Every record knows its author, write time, and supersession chain | Foundation for grounded, citable generation |

These eight principles are implementation-agnostic. A key-value store, a vector database, a neural adapter system, or a graph database could each approximate them. The CogMem Benchmark evaluates implementations on each principle independently.

---

## 4. Architecture

### 4.1 Design Philosophy

**Append-only is the design, not a constraint.** An update is a new record that references the old one. The full history of any value is always queryable.

**History is the asset.** For cognitive systems, *what something was* matters as much as *what it is now*. Belief transitions carry meaning.

**Multi-model native.** Graph, document, time-series, key-value, vector — each is a first-class citizen.

**Plug and play.** Any AI system connects through one universal API. One LLM Memory Protocol regardless of model or framework.

**Store anything.** Text, numbers, embeddings, binary, nested objects, graph edges, time-series points — one engine, no type restrictions.

### 4.2 The EmberRecord — Atomic Unit

Everything stored is an `EmberRecord`. Records are universal, immutable after creation, and carry rich metadata:

```
EmberRecord:
  id: UUID (permanent, never reused)
  namespace: str (logical grouping)
  record_type: document | node | edge | timeseries | vector | raw
  data: Any (the payload)

  # Time — always first-class
  created_at: datetime (write time, immutable)
  valid_from: datetime | None
  valid_until: datetime | None

  # Supersession chain
  superseded_by: UUID | None (newer version)
  supersedes: UUID | None (older version)

  # Epistemic status
  confidence: float [0, 1]
  decay_rate: float (Ebbinghaus decay parameter)
  access_count: int (reinforcement tracking)

  # Graph connections
  connections: list[EdgeRef]

  # Vector embedding
  embedding: list[float] | None

  # Annotations (the only form of "update")
  annotations: list[Annotation]

  # Provenance
  written_by: str (system | agent_id | user)
  origin: str | None
```

The `confidence` and `decay_rate` fields implement Ebbinghaus forgetting at the record level. The `access_count` provides spaced-repetition reinforcement — memories accessed more frequently decay more slowly.

### 4.3 Write Model — Append Only

| Traditional Database | Ember's Diaries |
|---|---|
| INSERT → row created | WRITE → record created |
| UPDATE → row mutated | WRITE → new record, old linked via `superseded_by` |
| DELETE → row destroyed | DEPRECATE → record flagged, never removed |

**Supersession.** When a value changes, a new record is written with `supersedes` pointing to the old record. The old record's `superseded_by` is set via a sidecar file — *the original record file is never modified.* The supersession chain is the full history.

**Deprecation.** Records are never deleted. Deprecation sets `valid_until` to now and writes a deprecation sidecar. The record remains queryable with `include_deprecated=True`.

**Annotations.** The only form of in-place "change." Annotations are separate objects stored alongside the record, never modifying it. They model reinterpretation — new understanding layered on old data.

### 4.4 Storage Layer

Records are stored as individual files (one `{UUID}.ember` file per record) serialized with MessagePack. This design provides:

**Crash safety.** Write-ahead log with `fsync()`. Protocol: (1) log to WAL as PENDING, (2) write record file via atomic rename, (3) mark WAL entry COMMITTED. Any PENDING entries without COMMITTED markers are replayed on startup.

**Thread safety.** `RLock` on all write paths. Multiple agents write concurrently.

**Immutability guarantee.** Record files are never modified after creation. Supersession, deprecation, and annotation state is stored in sidecar files.

### 4.5 Index Layer

Five indexes operate over the immutable record store:

**Master Index.** In-memory hash maps for O(1) lookups by ID, namespace, tag, and author. Persisted to JSON on checkpoint.

**Graph Index.** Bidirectional adjacency lists supporting BFS traversal, shortest path, and subgraph extraction. Edges carry type, weight, and temporal validity.

**Timeline Index.** Per-namespace sorted lists of (timestamp, record_id) pairs. Binary search for O(log n) range queries: `before()`, `after()`, `latest()`, `oldest()`.

**Vector Index.** Cosine similarity search with optional numpy acceleration. Namespace-scoped search, threshold filtering, and exclusion sets.

**Full-Text Index.** BM25-scored inverted index with automatic tokenization. Namespace filtering, exact phrase matching, and vocabulary statistics.

All indexes are updated synchronously on write — records are queryable immediately. Indexes rebuild from the record store on startup if empty, ensuring record files remain the source of truth.

### 4.6 Query Engine

The query engine provides a unified interface across all indexes:

- `query()` — Document query with field filters, tag matching, author filtering
- `search()` — BM25 full-text search returning scored results
- `similar()` — Cosine similarity vector search
- `neighbors()`, `path()`, `subgraph()` — Graph traversal
- `timeline()`, `latest()` — Time-range queries
- `history()` — Full supersession chain
- `hybrid_search()` — Combined text + vector search with weighted scoring

---

## 5. Cognitive Engine

The cognitive engine implements human-inspired memory processing as append-only operations. Every cognitive transformation creates new records or annotations — never mutating existing ones.

### 5.1 Confidence Decay (Ebbinghaus Forgetting)

Confidence decay is computed *at read time*, never stored. The effective confidence of a record is:

```
effective_confidence = base_confidence × exp(−effective_decay_rate × hours_since_access)
```

where:

```
effective_decay_rate = decay_rate / (1 + access_count × reinforcement_bonus)
```

This implements three phenomena from cognitive psychology simultaneously:

1. **Ebbinghaus forgetting curve** — Memory strength decays exponentially without reinforcement.
2. **Spaced repetition** — Records accessed more frequently decay more slowly.
3. **Immutable implementation** — The base confidence and decay rate are immutable properties of the record. Effective confidence is a pure function of the record's state and current time. No record is ever modified.

### 5.2 Episodic Segmentation

Inspired by EM-LLM [Fountas et al., 2024], the episodic segmenter groups streams of records into coherent episodes using multiple boundary signals:

1. **Temporal gap** — Large time gaps between consecutive records indicate episode boundaries.
2. **Topic shift** — Low Jaccard similarity between consecutive records' tag sets indicates a topic change.
3. **Namespace change** — Records in different namespaces belong to different episodes.
4. **Surprise** — Information-theoretic surprise based on tag rarity: `surprise(tag) = −log(P(tag))`. Rare tag combinations trigger boundaries.

Boundary detection uses a multi-signal approach: if any signal exceeds 0.9, it triggers a boundary; otherwise the weighted average of signals must exceed 0.5. This mimics multi-cue integration in human event segmentation.

### 5.3 Memory Consolidation

Following the Atkinson-Shiffrin model and LightMem's three-stage architecture, consolidation operates across three namespaces:

- **Sensory** (`sensory`) — Raw inputs, high volume, fast decay
- **Short-term** (`short_term`) — Filtered and grouped, medium retention
- **Long-term** (`long_term`) — Consolidated, high confidence, slow decay

Consolidation candidates are found by grouping records with overlapping tags and temporal proximity. Consolidated records carry boosted confidence, reduced decay rate, and `training_candidate=True` (suitable for fine-tuning data generation). Source records are never deprecated or modified — they remain independently queryable.

### 5.4 Conflict Detection

The conflict detector addresses the hallucination accumulation problem identified by HaluMem. Three detection strategies operate at write time:

1. **Value conflict** — A new record has a different value for the same field as an existing record in the same namespace.
2. **Temporal conflict** — Records have inconsistent `valid_from`/`valid_until` windows.
3. **Semantic conflict** — Two records have high embedding similarity (>0.85) but different data — indicating they describe the same thing differently.

When a conflict is detected, both records receive `Annotation` objects documenting the conflict, and a `Conflict` object is stored with type, severity, and resolution tracking. Critically, *conflicts are never silently resolved.* Both versions persist. The system's job is to detect and surface conflicts, not hide them.

### 5.5 Reflective Annotation (Metacognition)

The reflection engine implements automated metacognition — the system re-examining its own memories. Reflection produces `ReflectiveAnnotation` objects including the trigger, context at reflection time, and an insight score. These annotations create a layer of metacognitive commentary on the memory store — the system's record of its own thinking about its thinking.

---

## 6. LLM Integration Protocol

The `MemoryProtocol` is the single interface between a language model and Ember's Diaries:

```python
from embers.integration import MemoryProtocol

protocol = MemoryProtocol(db)

# Store (auto-embeds, auto-indexes, auto-checks conflicts)
protocol.remember("The user prefers dark mode", tags=["preference"])

# Retrieve relevant context (vector + text search, formatted for LLM)
context = protocol.recall("What are the user's preferences?")

# Verify epistemic status
protocol.verify(record_id, status="verified")

# Run cognitive processing (decay check, conflict scan, consolidation)
protocol.reflect()

# Deprecate (never delete)
protocol.forget(record_id, reason="No longer relevant")
```

### 6.1 Context Building

The `ContextBuilder` formats retrieved memories for LLM consumption with three modes:

**Text injection.** Structured text for system prompt insertion, with provenance metadata, confidence scores, and annotation summaries.

**Message injection.** Chat messages with per-message metadata (record ID, confidence, namespace) for chat completion APIs.

**Structured injection.** JSON format for function-calling models.

All modes respect a token budget and prioritize memories by effective confidence (after decay computation).

### 6.2 Embedding Pipeline

**Built-in mode.** TF-IDF embeddings (no external dependencies). Vocabulary is built from the record corpus, IDF weights are computed automatically, and embeddings are L2-normalized.

**Custom mode.** Any function `text → list[float]` can be provided (e.g., sentence-transformers, OpenAI embeddings).

---

## 7. Why Immutability Prevents Hallucination

The central claim of this work is that immutable memory storage is a structural defense against hallucination accumulation. Grounded in HaluMem findings:

**No extraction errors can compound.** In mutable systems, extracting a memory incorrectly and overwriting it loses the original. In Ember's Diaries, extraction creates a new record. The original remains. If the extraction was wrong, the original is still queryable.

**No update errors can compound.** In mutable systems, an incorrect update destroys the previous state. In Ember's Diaries, the previous state is always available via the supersession chain.

**Conflicts are visible, not hidden.** When two memories contradict, mutable systems must choose one. Ember's Diaries preserves both and flags the contradiction. The LLM can see that a fact is *contested* and adjust its confidence accordingly.

**Provenance is automatic.** Every record knows who wrote it, when, what it replaced, and what annotations have been added. This provenance trail is the foundation for grounded generation — the model can cite its sources.

**Epistemic status is queryable.** The `confidence` field, combined with read-time decay, gives the LLM a continuous signal about how trustworthy each memory is. A memory with `confidence=0.3` after decay should be presented differently than one with `confidence=0.95`.

---

## 8. Implementation

Ember's Diaries is implemented in Python 3.10+ with minimal dependencies (only `msgpack` for binary serialization).

```
embers/
├── core/          # Record, Annotation, EdgeRef, Types
├── engine/        # Writer, Reader, WAL
├── storage/       # Physical store, serialization
├── index/         # Master, Graph, Timeline, Vector, Full-text
├── query/         # Unified query engine
├── cognitive/     # Decay, Consolidation, Conflict, Episodic, Reflection
├── integration/   # ContextBuilder, EmbeddingPipeline, MemoryProtocol
└── namespace/     # Namespace manager
```

The test suite contains 101 tests across all layers. Installation: `pip install embers-diaries`.

---

## 9. Discussion and Future Work

### 9.1 Current Limitations

**No distributed mode.** The current implementation is single-node. A distributed protocol requires consensus on append order.

**No native GPU acceleration.** Vector search uses numpy but not CUDA. For production with millions of records, integration with FAISS or Annoy would be necessary.

**Built-in embeddings are basic.** The TF-IDF pipeline works for prototyping; real deployment should use sentence-transformers or similar.

**No formal benchmark results yet.** We have argued architecturally for hallucination prevention but have not yet run on HaluMem or similar benchmarks. This is a future work item for CogMem Benchmark.

### 9.2 Future Directions

**Memory³-style KV injection.** Convert Ember records to sparse attention KV pairs for direct injection into transformer self-attention layers.

**Formal hallucination evaluation.** Run on HaluMem, TruthfulQA, and LongMemEval to quantify the benefit of immutable memory.

**Distributed protocol.** Multi-node deployment with CRDTs for conflict-free replication (a natural fit for append-only stores).

**Training data pipeline.** The `training_candidate` field enables automatic construction of fine-tuning datasets from consolidated long-term memories, directly feeding Fig Engine's micro-training infrastructure.

**Cognitive scheduling.** Periodic background tasks for consolidation, reflection, and decay cleanup — similar to LightMem's sleep-time consolidation.

---

## 10. Conclusion

Ember's Diaries is not another memory system bolted onto an LLM. It is a foundational layer that reimagines what database semantics should look like for cognitive systems, and a behavioral specification that defines what any memory implementation must approximate.

By making immutability the core design principle, it provides structural guarantees against hallucination accumulation, full provenance tracking, and a natural substrate for cognitive processing. As a specification, it provides implementation-agnostic criteria against which any memory architecture — neural, database, or hybrid — can be evaluated.

The past is not a log. It is the database.

---

## References

1. Wang, Y., et al. "MEMORYLLM: Towards Self-Updatable Large Language Models." arXiv:2402.04624, 2024.
2. Wang, Y., et al. "M+: Extending MemoryLLM with Scalable Long-Term Memory." arXiv:2502.00592, 2025.
3. Yang, H., et al. "Memory³: Language Modeling with Explicit Memory." arXiv:2407.01178, 2024.
4. Fountas, Z., et al. "Human-inspired Episodic Memory for Infinite Context LLMs." arXiv:2407.09450, 2024.
5. Li, Z., et al. "MemOS: A Memory OS for AI System." arXiv:2507.03724, 2025.
6. HaluMem. "Evaluating Hallucinations in Memory Systems of Agents." arXiv:2511.03506, 2024.
7. LightMem. "Lightweight and Efficient Memory-Augmented Generation." arXiv:2510.18866, 2025.
8. Behrouz, A., et al. "Atlas: Learning to Optimally Memorize the Context at Test Time." arXiv:2505.23735, 2025.
9. MEGa. "Memorization and Knowledge Injection in Gated LLMs." arXiv:2504.21239, 2025.
10. Atkinson, R.C. & Shiffrin, R.M. "Human Memory: A Proposed System." 1968.
11. Ebbinghaus, H. "Über das Gedächtnis." 1885.
12. 0xticketguy (Harboria Labs). "Memory Fabric: Neural Weight-Space Implementation of Ember's Diaries." 2026.
13. 0xticketguy (Harboria Labs). "Fig Engine: CPU-Native Training Infrastructure for Large Language Models." 2026.
14. 0xticketguy (Harboria Labs). "CogMemBench: A Benchmark for Continuous Cognitive Memory in Large Language Models." 2026.

---

*Code: https://github.com/Harboria-Labs/embers-diaries*
*License: AGPL-3.0*
*Built by 0xticketguy / Harboria Labs*
