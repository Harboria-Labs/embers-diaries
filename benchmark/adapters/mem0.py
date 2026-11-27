"""Mem0 adapter stub. Not implemented — do not invent results."""

from __future__ import annotations

from .base import MemoryAdapter, NotImplementedAdapter


class Mem0Adapter(MemoryAdapter):
    name = "mem0"

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "Mem0 adapter is a stub (TODO). Wire it to the real Mem0 API and "
            "rerun `python -m benchmark.run --target mem0`. Do not invent results."
        ) from NotImplementedAdapter("mem0")

    def health(self) -> dict:  # pragma: no cover
        raise NotImplementedError

    def write(self, record: dict) -> dict:
        raise NotImplementedError

    def write_batch(self, records: list[dict], *, atomic: bool = False) -> dict:
        raise NotImplementedError

    def recall(self, query: str, *, namespace: str, top_k: int = 10, **kwargs) -> dict:
        raise NotImplementedError

    def close(self) -> None:
        return None
