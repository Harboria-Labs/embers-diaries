"""Letta adapter stub. Not implemented — do not invent results."""

from __future__ import annotations

from .base import MemoryAdapter, NotImplementedAdapter


class LettaAdapter(MemoryAdapter):
    name = "letta"

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "Letta adapter is a stub (TODO). Wire it to the real Letta API and "
            "rerun `python -m benchmark.run --target letta`. Do not invent results."
        ) from NotImplementedAdapter("letta")

    def health(self) -> dict:
        raise NotImplementedError

    def write(self, record: dict) -> dict:
        raise NotImplementedError

    def write_batch(self, records: list[dict], *, atomic: bool = False) -> dict:
        raise NotImplementedError

    def recall(self, query: str, *, namespace: str, top_k: int = 10, **kwargs) -> dict:
        raise NotImplementedError

    def close(self) -> None:
        return None
