"""Bounded fan-out event bus for the WebSocket stream.

Each subscriber gets its own bounded queue. A slow client is never allowed to
hold up the experiment loop: when its queue is full the oldest events are
discarded and the drop is counted, so the client can be told it lost events and
ask for a fresh snapshot rather than quietly rendering a gap.

Sequence numbers are assigned here, in one place, so they stay monotonic per
experiment.
"""

from __future__ import annotations

import asyncio
import itertools
from collections import deque
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from app.domain.enums import DataMode, EventSeverity, EventType
from app.domain.events import EventEnvelope

_event_counter = itertools.count(1)


class Subscriber:
    """One connected client."""

    def __init__(self, queue_limit: int, experiment_id: str | None) -> None:
        self.experiment_id = experiment_id
        self._queue: asyncio.Queue[EventEnvelope] = asyncio.Queue(maxsize=queue_limit)
        self.dropped = 0
        self._closed = False

    def offer(self, event: EventEnvelope) -> None:
        if self._closed:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            # Drop the oldest so the client keeps receiving current state.
            try:
                self._queue.get_nowait()
                self.dropped += 1
                self._queue.put_nowait(event)
            except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover - race
                self.dropped += 1

    async def stream(self) -> AsyncIterator[EventEnvelope]:
        while not self._closed:
            event = await self._queue.get()
            yield event

    def close(self) -> None:
        self._closed = True


class EventBus:
    """Publishes typed events to every interested subscriber."""

    def __init__(self, history_limit: int, subscriber_queue_limit: int) -> None:
        self._subscribers: set[Subscriber] = set()
        self._history: deque[EventEnvelope] = deque(maxlen=history_limit)
        self._queue_limit = subscriber_queue_limit
        self._sequences: dict[str, itertools.count[int]] = {}
        self._last_sequence: dict[str, int] = {}

    # -- subscription ---------------------------------------------------

    def subscribe(self, experiment_id: str | None = None) -> Subscriber:
        subscriber = Subscriber(self._queue_limit, experiment_id)
        self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        subscriber.close()
        self._subscribers.discard(subscriber)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    # -- publishing -----------------------------------------------------

    def next_sequence(self, experiment_id: str | None) -> int:
        """Consume and return the next sequence number for this experiment."""
        key = experiment_id or "__global__"
        counter = self._sequences.get(key)
        if counter is None:
            counter = itertools.count(1)
            self._sequences[key] = counter
        value = next(counter)
        self._last_sequence[key] = value
        return value

    def peek_sequence(self, experiment_id: str | None) -> int:
        """Return the last sequence number issued without consuming one."""
        return self._last_sequence.get(experiment_id or "__global__", 0)

    def publish(
        self,
        *,
        event_type: EventType,
        experiment_id: str | None,
        data_mode: DataMode,
        payload: dict[str, Any] | None = None,
        summary: str = "",
        severity: EventSeverity = EventSeverity.INFO,
        timestamp: datetime | None = None,
    ) -> EventEnvelope:
        event = EventEnvelope(
            event_id=f"evt-{next(_event_counter):06d}",
            experiment_id=experiment_id,
            timestamp=timestamp or datetime.now(UTC),
            data_mode=data_mode,
            type=event_type,
            sequence=self.next_sequence(experiment_id),
            severity=severity,
            summary=summary,
            payload=payload or {},
        )
        self._history.append(event)
        for subscriber in self._subscribers:
            if subscriber.experiment_id in (None, experiment_id):
                subscriber.offer(event)
        return event

    # -- history --------------------------------------------------------

    def recent(
        self,
        limit: int = 100,
        experiment_id: str | None = None,
        types: set[EventType] | None = None,
    ) -> list[EventEnvelope]:
        events = list(self._history)
        if experiment_id is not None:
            events = [e for e in events if e.experiment_id == experiment_id]
        if types is not None:
            events = [e for e in events if e.type in types]
        return events[-limit:]

    @property
    def total_events(self) -> int:
        return len(self._history)

    def clear(self, experiment_id: str | None = None) -> None:
        if experiment_id is None:
            self._history.clear()
            self._sequences.clear()
            self._last_sequence.clear()
            return
        remaining = [e for e in self._history if e.experiment_id != experiment_id]
        self._history.clear()
        self._history.extend(remaining)
        self._sequences.pop(experiment_id, None)
        self._last_sequence.pop(experiment_id, None)
