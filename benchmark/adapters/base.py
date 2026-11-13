"""Adapter interface so the same dataset/tasks can run against other memory systems."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class NotImplementedAdapter(RuntimeError):
    """Raised when a stub adapter is selected. Never invent comparison results."""


class MemoryAdapter(ABC):
    """Minimal contract every backend must implement for the shared tasks."""

    name: str = "base"

    @abstractmethod
    def health(self) -> dict:
        ...

    @abstractmethod
    def write(self, record: dict) -> dict:
        ...

    @abstractmethod
    def write_batch(self, records: list[dict], *, atomic: bool = False) -> dict:
        ...

    @abstractmethod
    def recall(self, query: str, *, namespace: str, top_k: int = 10, **kwargs) -> dict:
        ...

    @abstractmethod
    def close(self) -> None:
        ...


class TimedResult(dict):
    """A response dict that always carries latency_ms and status."""

    @property
    def ok(self) -> bool:
        status = self.get("status")
        return bool(self.get("ok", False)) or (isinstance(status, int) and 200 <= status < 300)
