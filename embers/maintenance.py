"""Opt-in reflect+consolidate cycle. Off unless interval env is set."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def run_maintenance_cycle(protocol, namespaces: list[str]) -> dict:
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
    import asyncio
    while True:
        try:
            summary = run_maintenance_cycle(protocol, namespaces)
            logger.info("maintenance cycle: %s", summary)
        except Exception as e:
            logger.warning("maintenance cycle raised unexpectedly: %s", e)
        await asyncio.sleep(interval_seconds)
