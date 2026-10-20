# Control Center: safe read-only foundation

Inspected Candidate 04 `6d325c0eaf5a2fe7e8c89d6b5c51781a1a2ba8bb`.
Existing Track 7/Lobby milestones are preserved.

## Reused mechanisms

Typed `embers/config.py` TOML/environment validation, normal FastAPI server,
agent registry/token authentication, namespace ACLs, `consolidated.settings`
configuration projection/history, and existing proposal/conflict readers.
Research configuration already has an authorized FUR-ledger configuration action,
request identity, revision checking and durable history. Runtime TOML configuration
has startup loading but no authenticated browser write/staging/apply service.
The existing Promotion Engine has admission modes and durable decisions, but no
verified human administrator role. The legacy authenticated agent `promote` route
is not evidence of human authority and is not wired to a human approval button.

## Implemented

- Separate charcoal/light-responsive Control Center shell at `/control`.
- Validated `api.control_path` or `EMBER_CONTROL_PATH` selects one lowercase path
  segment; reserved API/observer paths, traversal and URL syntax are rejected.
  This is startup configuration; restart after changes.
- `<path>/state?namespace=...` authenticates existing agent credentials, checks
  namespace READ before querying, and checks derivation access before conflict
  inspection. It returns pending proposals, recorded agent, evidence type/integrity
  availability, bounded derivation/conflict samples, research settings, schema
  descriptions, revision and recorded configuration history.
- GET shell/assets are public and contain no private state. State requires
  `X-Ember-Agent-Id` and `X-Ember-Token`. Inputs are not saved in browser storage;
  token field is cleared after each request. No credentials enter URLs/payloads.
- Escaped text rendering, restrictive CSP, no framing, no caching, no referrers,
  same-origin script/connect policy. No browser administrative mutation exists.
- `<path>/configuration` and `<path>/decision` POST always return 403, including
  for valid agent credentials. Observer capabilities cannot authenticate reads
  and never confer write rights. No new privilege or permanent identity exists.
- UI clearly distinguishes immediate existing research changes (existing
  authorized Research Settings interface) from operator-managed runtime settings
  requiring restart. Pending restart is **Not observed**, not invented. Current
  sensitive runtime values, paths, endpoints, sessions and credentials are omitted.

## DEPENDENCY FOUND

**Blocked:** human administrative configuration edits, human approval/rejection,
remote runtime config persistence and staged-restart tracking.

**Why:** the repository authenticates agents and grants namespace/research rights;
it does not establish verified human administrator credentials/roles or a runtime
configuration write authority. Treating an ordinary token as human approval would
weaken the requested security boundary. The implementation adds no such shortcut.

**Smallest decision needed:** approve how an operator is authenticated and which
configuration/review operations that authority may perform, using existing
namespace and promotion safeguards. Runtime config persistence/staging must also
be specified before a browser can write it. No identity/account model was added.

No permitted human decision was exercised or newly audited because no such
verified authority exists. Existing research configuration history is displayed;
existing decisions/audits remain unchanged. This is a functional **read-only
foundation**, not completion of the blocked administrative milestone.

## Scope/limits

50 displayed proposals, 20 evidence metadata entries and derivation IDs per
proposal, 50 conflict entries, existing 30-entry configuration history. These cap
returned samples, not backend scan CPU. No raw evidence artifact bodies or bearer
session provenance are returned. Proposal discovery/reasons remain user-authored
text, rendered as text, not executable HTML. No automatic polling or SSE client
was introduced. Observatory transport and authorization are untouched.

Runtime settings catalogue/editing, human decisions, automatic restart, and remote
operator authorization remain blocked. Visual layout has source/HTTP checks only;
no browser execution or live deployment is claimed.

## Startup and access

Use the existing environment/native installation and normal server:

```sh
ember-api --config config.toml
```

or `python -m embers.api_cli --config config.toml`. In `[api]` set
`control_path = "/control"` (or a validated alternative). Default listen address
is `127.0.0.1:9200`, so the expected local URL is
`http://127.0.0.1:9200/control`; this is an instruction, not a verified live link.
Use the configured host/port/path if changed. Supply existing agent credentials
and a namespace for read-only inspection. Custom paths are not authentication.
For remote read access use the existing deployment's HTTPS reverse proxy and
explicit bind/network configuration; do not transmit tokens over plain HTTP.
There is no remote administrative write capability in this milestone.

Validation: `python -m pytest tests/test_control_center.py tests/test_config.py tests/test_track7_batch2.py tests/test_research_observer.py -q --tb=short` — **65 passed**, one dependency deprecation warning. Checks include invalid/custom paths, missing credentials, private namespace denial, rejection of observer/agent administrative writes, proposal/evidence projection and unchanged physical record hashes after reads/denied writes. Existing configuration validation, promotion safeguards and observer tests also ran. No full-suite or permitted human-write claim.
