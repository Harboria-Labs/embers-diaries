"""
Ember's Diaries — Maintenance Scheduler (spec §12/§14)

Reflection and consolidation both existed as fully-built, correct engines
with no scheduler anywhere calling them — reachable only by a human or
agent explicitly deciding to invoke them. This module is the missing
"periodically" — but deliberately stays inert unless explicitly turned on.

That's a design choice, not an oversight: the architecture principle
throughout is "the agent controls memory, Ember provides infrastructure."
A background loop that starts mutating/annotating memories on its own the
moment a server boots would quietly violate that — the loop needs to be
something a human or operator opted into, not a default. See
embers/api/__init__.py for how this gets wired into the running server,
gated behind EMBER_MAINTENANCE_INTERVAL_SECONDS (unset = disabled).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def run_maintenance_cycle(protocol, namespaces: list[str]) -> dict:
    """Run one reflection + consolidation pass over each given namespace.

    Each namespace is handled independently — one namespace's failure
    doesn't stop the others. Returns a per-namespace summary; never raises.
    """
    results: dict[str, dict] = {}
    for ns in namespaces:
        entry: dict = {}
        try:
            annotations = protocol.reflect(namespace=ns)
            entry["reflections"] = len(annotations)
        except Exception as e:
            entry["reflect_error"] = f"{type(e).__name__}: {e}"
            logger.warning("maintenance: reflect(%s) failed: %s", ns, e)

        try:
            consolidated = protocol.consolidate(namespace=ns)
            entry["consolidated"] = len(consolidated)
        except Exception as e:
            entry["consolidate_error"] = f"{type(e).__name__}: {e}"
            logger.warning("maintenance: consolidate(%s) failed: %s", ns, e)

        results[ns] = entry
    return results


async def maintenance_loop(protocol, namespaces: list[str], interval_seconds: int):
    """Background loop: run_maintenance_cycle() every interval_seconds,
    forever, until cancelled. Intended to be launched as a single
    asyncio task from the server's startup — see embers/api/__init__.py.
    Never raises out of the loop body; a single bad cycle is logged and
    the loop keeps running on the next interval."""
    import asyncio
    while True:
        try:
            summary = run_maintenance_cycle(protocol, namespaces)
            logger.info("maintenance cycle: %s", summary)
        except Exception as e:
            logger.warning("maintenance cycle raised unexpectedly: %s", e)
        await asyncio.sleep(interval_seconds)
