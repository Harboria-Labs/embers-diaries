"""Authenticated transport adapters for explicitly configured relevance services."""
from dataclasses import asdict

from ..cognitive.feedback_replay import Credit, Dependency, RelevanceDecision


def configured_service(db, namespace, context_id, actor, operation="read"):
    db.require_namespace_access(namespace, actor, operation)
    service = getattr(db, "_relevance_services", {}).get((namespace, context_id))
    if service is None:
        raise KeyError("relevance service is not configured for this context")
    return service


def parse_decision(body):
    if not isinstance(body, dict):
        raise ValueError("decision must be an object")
    allowed = {"outcome_id", "status", "credits", "depends_on", "report_ids", "reason"}
    if set(body) - allowed:
        raise ValueError("unsupported decision fields")
    try:
        return RelevanceDecision(
            outcome_id=body["outcome_id"], status=body["status"],
            credits=tuple(Credit(**c) for c in body.get("credits", [])),
            depends_on=tuple(Dependency(**d) for d in body.get("depends_on", [])),
            report_ids=tuple(body.get("report_ids", [])),
            reason=body.get("reason", ""),
        )
    except (KeyError, TypeError) as error:
        raise ValueError("malformed relevance decision") from error


def resolve(db, namespace, context_id, actor, body):
    if not isinstance(body, dict) or set(body) != {"decision", "request_id", "expected_revision"}:
        raise ValueError("supply decision, request_id and expected_revision only")
    service = configured_service(db, namespace, context_id, actor, "write")
    revision = service.resolve(
        parse_decision(body["decision"]), actor=actor,
        request_id=body["request_id"], expected_revision=body["expected_revision"],
    )
    return asdict(revision)


def projection(db, namespace, context_id, actor):
    result = configured_service(db, namespace, context_id, actor).project()
    output = asdict(result)
    output["pair_state"] = [
        {"from_version": source, "to_version": target, "relation": relation,
         "signed_state": value, "weight": max(0.0, value)}
        for (source, target, relation), value in sorted(result.pair_state.items())
    ]
    return output
