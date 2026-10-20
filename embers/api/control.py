"""Read-only Control Center until verified human administrative authority exists.

Reuse namespace ACLs and existing projections. No observer grants, decision writes,
configuration files or administrative identities are accepted/created here.
"""
from pathlib import Path
from fastapi import Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from ..integration.consolidated import settings
from ..core.types import ProposalStatus

HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
           "X-Content-Type-Options": "nosniff",
           "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"}
BLOCKER = "Verified human administrator authority is not implemented. Administrative writes and human approval are disabled."


def install(app, path):
    async def page():
        return HTMLResponse(Path(__file__).with_name('control.html').read_text().replace('PLACEHOLDER', path), headers=HEADERS)

    async def script():
        return HTMLResponse(Path(__file__).with_name('control.js').read_text(),
                            media_type='text/javascript', headers=HEADERS)

    async def style():
        return HTMLResponse(Path(__file__).with_name('control.css').read_text(),
                            media_type='text/css', headers=HEADERS)

    async def state(namespace: str, x_ember_agent_id: str | None = Header(None),
                    x_ember_token: str | None = Header(None)):
        from . import _get_db, v1
        db = _get_db()
        actor = v1.require_agent(db, x_ember_agent_id, x_ember_token).agent_id
        try:
            db.require_namespace_access(namespace, actor, 'read')
            config = settings(db, namespace, actor)
            # Advertise existing agent permissions separately from human authority.
            rows = []
            proposals = db.proposals(namespace, status=ProposalStatus.PENDING)
            for proposal in proposals[:50]:
                conflicts = []
                for source in proposal.derivation[:20]:
                    conflicts.extend({'id': c.conflict_id, 'status': c.status.value, 'winner_id': c.winner_id}
                                     for c in db.conflicts_for(source, include_closed=True, caller=actor))
                rows.append(dict(proposal_id=proposal.proposal_id, discovery=proposal.discovery,
                    reason=proposal.reason, status=proposal.status.value, agent_id=proposal.agent_id,
                    confidence=proposal.confidence, evidence_count=len(proposal.evidence),
                    evidence=[{'source_type': e.source_type.value, 'integrity_valid': e.verify_integrity()}
                              for e in proposal.evidence[:20]],
                    derivation=proposal.derivation[:20], conflict_statuses=conflicts[:50],
                    uncertainty='Admission confidence is not verified truth.'))
            return JSONResponse(dict(namespace=namespace, actor=actor, blocker=BLOCKER,
                human_admin=False, research_settings=config, proposals=rows,
                proposal_limit=50, truncated=len(proposals)>50,
                runtime_configuration={'change_mode':'Local operator edits existing TOML/environment; restart required.',
                    'pending_restart':'Not observed: no staged runtime configuration authority exists.'}), headers=HEADERS)
        except PermissionError as exc:
            raise HTTPException(403, 'Namespace or derivation access denied') from exc

    async def blocked():
        raise HTTPException(403, BLOCKER)

    app.add_api_route(path, page, methods=['GET'], include_in_schema=False)
    app.add_api_route(path+'/control.js', script, methods=['GET'], include_in_schema=False)
    app.add_api_route(path+'/control.css', style, methods=['GET'], include_in_schema=False)
    app.add_api_route(path+'/state', state, methods=['GET'])
    app.add_api_route(path+'/configuration', blocked, methods=['POST'])
    app.add_api_route(path+'/decision', blocked, methods=['POST'])
