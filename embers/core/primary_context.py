"""Contract 01: opaque agent-supplied context, without canonicalization."""

def validate_context(value):
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError("primary/retrieval context must be a nonempty string or null")
    return value
