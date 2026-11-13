"""Zep adapter stub. Not implemented — do not invent results."""

from __future__ import annotations

from .base import MemoryAdapter, NotImplementedAdapter


class ZepAdapter(MemoryAdapter):
    name = "zep"

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "Zep adapter is a stub (TODO). Wire it to the real Zep API and "
            "rerun `python -m benchmark.run --target zep`. Do not invent results."
        ) from NotImplementedAdapter("zep")

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
