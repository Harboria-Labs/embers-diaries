"""Process-local fan-out for ephemeral lobby events."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
import uuid


@dataclass
class LobbySubscription:
    subscription_id: str
    board_id: str
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue
    broker: "LobbyEventBroker"

    async def get(self) -> dict:
        return await self.queue.get()

    def close(self) -> None:
        self.broker.unsubscribe(self)


class LobbyEventBroker:
    """Fan lobby events out to bounded queues without persisting them."""

    def __init__(self, *, queue_size: int = 128):
        self.queue_size = queue_size
        self._lock = RLock()
        self._sequences: dict[str, int] = {}
        self._subscribers: dict[str, dict[str, LobbySubscription]] = {}

    def subscribe(self, board_id: str) -> LobbySubscription:
        subscription = LobbySubscription(
            subscription_id=str(uuid.uuid4()),
            board_id=board_id,
            loop=asyncio.get_running_loop(),
            queue=asyncio.Queue(maxsize=self.queue_size),
            broker=self,
        )
        with self._lock:
            self._subscribers.setdefault(board_id, {})[
                subscription.subscription_id
            ] = subscription
        return subscription

    def unsubscribe(self, subscription: LobbySubscription) -> None:
        with self._lock:
            board_subscribers = self._subscribers.get(subscription.board_id)
            if board_subscribers is None:
                return
            board_subscribers.pop(subscription.subscription_id, None)
            if not board_subscribers:
                self._subscribers.pop(subscription.board_id, None)

    def current_sequence(self, board_id: str) -> int:
        with self._lock:
            return self._sequences.get(board_id, 0)

    def publish(self, board_id: str, event_type: str, data: dict) -> dict:
        with self._lock:
            sequence = self._sequences.get(board_id, 0) + 1
            self._sequences[board_id] = sequence
            subscribers = list(self._subscribers.get(board_id, {}).values())
        event = {
            "type": event_type,
            "board_id": board_id,
            "sequence": sequence,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "data": deepcopy(data),
        }
        for subscription in subscribers:
            try:
                subscription.loop.call_soon_threadsafe(
                    self._enqueue, subscription, event)
            except RuntimeError:
                self.unsubscribe(subscription)
        return event

    def reset(self) -> None:
        with self._lock:
            self._subscribers.clear()
            self._sequences.clear()

    @staticmethod
    def _enqueue(subscription: LobbySubscription, event: dict) -> None:
        queue = subscription.queue
        outgoing = event
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            outgoing = {**event, "resync_required": True}
        try:
            queue.put_nowait(outgoing)
        except asyncio.QueueFull:
            pass
