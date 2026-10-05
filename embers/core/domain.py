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
    """Pre-ELLA bounded truth rendering for compatibility/capacity only.

    This is intentionally not the canonical epistemic projection. It preserves
    the historical inline shape so changing ELLA evidence cannot change memory
    admission merely by enlarging or shortening truth metadata.
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
