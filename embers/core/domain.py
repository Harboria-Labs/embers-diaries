"""Serialization-only facade over the authoritative Rust domain; no Python fallback."""
import json
from embers._native import domain_call

MODEL_VERSION = 'ember-fur-activation-v1'

def call(operation, value):
    return json.loads(domain_call(operation, json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)))


def explicit_truth(record, open_conflict=False):
    provider=getattr(record,'_epistemic_provider',None)
    return call('truth', {'data':record.data, 'projection':provider() if provider else None, 'open_conflict':open_conflict})
