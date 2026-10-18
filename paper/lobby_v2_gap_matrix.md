# Lobby V2 source-to-contract gap matrix

Status: 2026-10-18 source review. The ten principles and five architectural decisions in [lobby_v2_architecture.md](lobby_v2_architecture.md) are approved; each V2 implementation capability remains pending its batch evidence.

| Capability | Current source | V2 disposition |
| --- | --- | --- |
| Shared working scope | `lobby/store.py` boards keyed by task, namespace, room; `context.py` resolves a namespace | **Extend.** Require explicit shared-task membership/consent; matching strings do not authorize access. |
| Agent/session identity | `db.py` sessions; `mcp/session_auth.py`; REST `_lobby_context` | **Reuse and align.** Bind active owned session on every V2 route/tool, including token-plus-session. |
| Namespace ACL | `NamespaceManager`; `db.require_namespace_access` | **Reuse and strengthen.** Existing unregistered namespace is open by default; add a shared-scope gate and check ACL on publish, query, stream and artifact access. |
| Capture and evidence | Agent-authored `publish`; `Evidence`, proposal evidence, observation/request provenance | **Extend.** Add explicit completion selection, evidence availability and authenticated source references; no tool transcript capture exists in Ember itself. |
| Working-memory structure | In-memory posts with type/body/approach/time/corroborations/`promoted_to` | **Extend.** Add separate claim, uncertainty, evidence refs, task/scope, lifecycle/version and idempotency; avoid durable record creation at publication. |
| Contextual retrieval | Board snapshot/polling; durable `MemoryProtocol.recall`, query indexes, FUR/Pairing/ELLA | **New narrow provisional query.** Reuse context inputs and bounded ordering ideas; do not put provisional items through durable recall or truth/usefulness ledgers. |
| Real-time delivery | `/v1/lobby/updates` snapshot polling; observation SSE/journal and observer stream | **New Lobby feed using existing SSE patterns.** Separate event source, cursor/reset, authorization and bounded catch-up; Observatory remains read only/zero causal effect. |
| Promotion | `lobby_surface._promote` makes proposal/failure with post/corroboration evidence; Promotion Engine applies policy | **Reuse, then harden.** Preserve proposal/conflict/admission path; distinguish post testimony from claim proof and make handoff retry-safe. |
| Expiry and close | `LobbyStore` TTL drops posts; close marks board closed before writing failures | **Extend.** Explicit correction/supersession/expiry; retain accepted handoff receipts and reconcile partial close/promotion failures. |
| Agent integration | REST/MCP Lobby calls; no general harness completion hook or background wake contract | **Extend at boundary.** Manual submit/query works first; each harness separately proves capture and push hooks. |

The Observatory's read-only journal, access grants and research event model are deliberately **separate** from Lobby's causal working-memory submissions. Track 7 is closed at its local milestone and has no V2 dependency or planned change here.
