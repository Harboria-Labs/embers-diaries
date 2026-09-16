"""Shared proposal listing shape for MCP and REST."""


def proposal_listing(p) -> dict:
    evidence = []
    for ev in p.evidence or []:
        if hasattr(ev, "to_dict"):
            evidence.append(ev.to_dict())
        elif isinstance(ev, dict):
            evidence.append(ev)
    authors = sorted({row.get("agent_id") for row in evidence if row.get("agent_id")})
    return {
        "proposal_id": p.proposal_id,
        "discovery": p.discovery,
        "reason": p.reason,
        "confidence": p.confidence,
        "status": p.status.value,
        "agent_id": p.agent_id,
        "evidence_count": len(evidence),
        "evidence_authors": authors,
        "evidence_author_count": len(authors),
        "evidence": evidence,
    }
