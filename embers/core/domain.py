"""Serialization-only facade over the authoritative Rust domain; no Python fallback."""
import json
from embers._native import domain_call

MODEL_VERSION = 'ember-fur-activation-v1'

def call(operation, value):
    return json.loads(domain_call(operation, json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)))


def explicit_truth(record, open_conflict=False):
    return call('truth', {'data':record.data, 'annotations':[a.to_dict() for a in record.annotations], 'open_conflict':open_conflict})
