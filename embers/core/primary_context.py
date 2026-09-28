"""Contract 01 compatibility facade; Rust owns opaque context invariants."""
from .domain import call

def validate_context(value):
    return call('context', value)
