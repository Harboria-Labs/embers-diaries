"""Serialization-only facade over the authoritative Rust domain; no Python fallback."""
import json
from embers._native import domain_call

MODEL_VERSION = 'ember-fur-activation-v1'

def call(operation, value):
    return json.loads(domain_call(operation, json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)))


def explicit_truth(record, open_conflict=False):
    provider=getattr(record,'_epistemic_provider',None)
    return call('truth', {'data':record.data, 'projection':provider() if provider else None, 'open_conflict':open_conflict})


def historical_inline_truth(record, open_conflict=False):
    """Pre-ELLA rendering retained only for legacy compatibility helpers.

    This is never canonical epistemic authority and must not be used to decide
    ELLA state. New research recall keeps canonical ELLA state in separate
    metadata so this historical projection cannot affect admission capacity.
    """
    data=record.data if isinstance(record.data,dict) else {}
    status='unverified';source='unset'
    allowed={'verified','hypothesis','unverified','contested','deprecated','incorrect','provisional','disputed','superseded'}
    for key in ('_status','verify_status'):
        value=data.get(key)
        if value in allowed:
            status=value;source=key;break
    for ann in getattr(record,'annotations',[]) or []:
        if getattr(ann,'annotation_type',None)=='validation' and getattr(ann,'context',None)=='verification':
            tags=[t for t in (getattr(ann,'tags',[]) or []) if t in {'verified','hypothesis','unverified','contested','deprecated','incorrect'}]
            if len(tags)==1:
                status=tags[0];source='verification_annotation'
    if open_conflict:
        status='contested';source='open_conflict'
    return {'status':status,'source':source,'projection_version':'explicit-epistemic-v1'}


def epistemically_neutral_data(data):
    """Remove legacy truth markers from model-facing memory content.\n\n    Stored records remain untouched for compatibility/audit. Canonical truth is\n    exposed only through ELLA projection surfaces.\n    """
    if not isinstance(data, dict):
        return data
    return {k:v for k,v in data.items() if k not in ("_status","verify_status")}


def public_epistemic_summary(record):
    """Bounded, non-causal ELLA summary for ordinary API/MCP record reads.

    Ordinary memory visibility must never fail merely because the separate
    epistemic projection is temporarily unavailable. Dedicated ELLA endpoints
    still surface projection errors directly for diagnosis.
    """
    try:
        projection=explicit_truth(record)
    except Exception:
        return {"available":False,"error":"epistemic_projection_unavailable"}
    keys=("base_epistemic_verdict","public_epistemic_state","score","support_mass",
          "opposition_mass","raw_evidence_count","accepted_unit_count",
          "hard_collapsed_count","soft_cluster_count","unresolved_independence_count",
          "dangling_dependency_count","lineage_coverage","assessment_started",
          "epistemic_revision","evidence_dispute","conflict_overlay")
    return {"available":True,**{k:projection.get(k) for k in keys}}
