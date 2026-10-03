"""Candidate 04 numerical kernels, not an automatic feedback consumer.

Callers must resolve outcome identity, credit, authority and corrections before
applying an update. Replaying raw reports (or retrying an update) is NOT safe.
These functions have no access to truth, storage, clocks or retrieval side effects.
"""

from __future__ import annotations

import math

from ..core.feedback import Feedback, FeedbackChannel

DYNAMICS_VERSION = "candidate-04-kernels-v1"


def _bounded(name: str, value: float, low: float, high: float) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if not low <= value <= high:
        raise ValueError(f"{name} must be within [{low}, {high}]")
    return float(value)


def bounded_update(current: float, credit: float, rate: float) -> float:
    """One resolved relevance contribution: clip(current + rate * credit)."""
    current = _bounded("current", current, -1, 1)
    credit = _bounded("credit", credit, -1, 1)
    rate = _bounded("rate", rate, 0, 1)
    return max(-1.0, min(1.0, current + rate * credit))


def pair_weight(signed_state: float) -> float:
    """Retrieval uses only the positive part; retain negative learning state."""
    return max(0.0, _bounded("signed_state", signed_state, -1, 1))


def preview_memory_bias(current: float, feedback: Feedback, rate: float) -> float:
    """Non-persistent preview for one memory report, never a replay pipeline.

    Correctness reports cannot change contextual bias. Legacy mixed reports are
    deliberately ineligible. A preview neither accepts an outcome nor verifies
    a claim; do not store it as learned state without canonical resolution.
    """
    feedback.validate()
    current = _bounded("current", current, -1, 1)
    rate = _bounded("rate", rate, 0, 1)
    if feedback.schema_version != 2:
        raise ValueError("legacy feedback is not eligible for candidate learning")
    if FeedbackChannel(feedback.channel) == FeedbackChannel.CORRECTNESS:
        return current
    return bounded_update(current, feedback.signal, rate)


def activation_step(
    active: float, *, direct: float, cue: float, bias: float,
    elapsed: float, epsilon: float, mu: float, delta: float, rho: float,
) -> tuple[float, float]:
    """Exact constant-input active/latent transfer over explicit model time.

    Returns (A, L), with A+L=1 and floors epsilon. No production defaults:
    callers must version parameters and define what one time unit means.
    """
    epsilon = _bounded("epsilon", epsilon, 0, 0.5)
    mu = _bounded("mu", mu, 0, 1)
    if not 0 < epsilon < 0.5 or mu == 1:
        raise ValueError("require 0 < epsilon < .5 and 0 <= mu < 1")
    active = _bounded("active", active, epsilon, 1 - epsilon)
    direct = _bounded("direct", direct, 0, 1)
    cue = _bounded("cue", cue, direct, 1)
    bias = _bounded("bias", bias, -1, 1)
    for name, value in (("elapsed", elapsed), ("delta", delta), ("rho", rho)):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
    if elapsed < 0 or delta <= 0 or rho <= 0:
        raise ValueError("require elapsed >= 0, delta > 0, rho > 0")
    u = rho * cue * (1 + mu * bias)
    v = rho * ((1 - direct) * (1 - mu * bias) + delta)
    total = u + v
    if not math.isfinite(total) or total <= 0:
        raise ValueError("rates exceed numeric range")
    equilibrium = epsilon + (1 - 2 * epsilon) * (u / total)
    # expm1 keeps very small intervals accurate; overflow to +inf is safe here.
    fraction = -math.expm1(-total * elapsed)
    updated = active + (equilibrium - active) * fraction
    updated = max(epsilon, min(1 - epsilon, updated))
    return updated, 1 - updated
