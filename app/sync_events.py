"""Thread-safe event broker for streaming live sync progress to SSE clients.

The broker captures a bounded history of events for the current or most recent
run so clients that connect mid-sync can replay what they missed, and late
connectors can still see the final summary.
"""

from __future__ import annotations

import itertools
import logging
import queue
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# Event types — kept in one place so client JS and Python agree.
EVT_STARTED = "sync.started"
EVT_PHASE = "sync.phase"
EVT_SOURCE_START = "sync.source.start"
EVT_SOURCE_END = "sync.source.end"
EVT_LIBRARY = "sync.library"
EVT_COMPARE = "sync.compare"
EVT_SHOW = "sync.show"
EVT_LOG = "sync.log"
EVT_FINISHED = "sync.finished"
EVT_ERROR = "sync.error"


@dataclass(frozen=True)
class SyncEvent:
    """An ordered, serializable sync event with stable id and type."""

    id: int
    type: str
    data: dict
    ts: float = field(default_factory=lambda: time.time())


class SyncEventBroker:
    """Fan-out broker for sync events.

    Subscribers receive every event emitted after they subscribe, plus any
    replayable events from the current run buffer (last ``buffer_size``).
    The broker is safe to call from background threads; subscribers read
    from their own :class:`queue.Queue` so a slow consumer cannot block
    producers.
    """

    def __init__(self, buffer_size: int = 500) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue[SyncEvent | None]] = []
        self._buffer: deque[SyncEvent] = deque(maxlen=buffer_size)
        self._counter = itertools.count(1)
        self._run_id: int = 0

    def new_run(self) -> int:
        """Start a new run; clears the replay buffer and bumps run id."""
        with self._lock:
            self._run_id += 1
            self._buffer.clear()
            return self._run_id

    @property
    def current_run_id(self) -> int:
        with self._lock:
            return self._run_id

    def emit(self, event_type: str, data: dict | None = None) -> SyncEvent:
        """Publish an event to all subscribers and the replay buffer."""
        payload = dict(data or {})
        payload.setdefault("run_id", self.current_run_id)
        event = SyncEvent(id=next(self._counter), type=event_type, data=payload)

        with self._lock:
            self._buffer.append(event)
            subscribers = list(self._subscribers)

        for q in subscribers:
            try:
                q.put_nowait(event)
            except queue.Full:
                # Drop silently — a backed-up client will be disconnected by
                # the SSE handler when its queue fills.
                log.debug("Dropping sync event for saturated subscriber")
        return event

    def snapshot(self, after_id: int = 0) -> list[SyncEvent]:
        """Return buffered events strictly after ``after_id`` (id > after_id)."""
        with self._lock:
            return [evt for evt in self._buffer if evt.id > after_id]

    def subscribe(
        self, maxsize: int = 1000, after_id: int = 0
    ) -> tuple[queue.Queue[SyncEvent | None], Callable[[], None]]:
        """Register a subscriber queue. Returns ``(queue, unsubscribe)``.

        The initial replay (events from the buffer with id > ``after_id``)
        is placed on the queue before any live events.
        """
        q: queue.Queue[SyncEvent | None] = queue.Queue(maxsize=maxsize)

        with self._lock:
            for event in self._buffer:
                if event.id > after_id:
                    try:
                        q.put_nowait(event)
                    except queue.Full:
                        break
            self._subscribers.append(q)

        def _unsubscribe() -> None:
            with self._lock, suppress(ValueError):
                self._subscribers.remove(q)
            # Wake up any blocking reader so it can exit cleanly.
            with suppress(queue.Full):
                q.put_nowait(None)

        return q, _unsubscribe

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


# Helper type alias for the emitter callable passed into run_sync.
EventEmitter = Callable[[str, dict], None]


def make_emitter(broker: SyncEventBroker | None) -> EventEmitter:
    """Return a best-effort emitter that never raises into sync code."""
    if broker is None:
        def _noop(_type: str, _data: dict) -> None:
            return None

        return _noop

    def _emit(event_type: str, data: dict) -> None:
        try:
            broker.emit(event_type, data)
        except Exception:
            log.exception("Failed to emit sync event %s", event_type)

    return _emit


__all__: Iterable[str] = (
    "EVT_COMPARE",
    "EVT_ERROR",
    "EVT_FINISHED",
    "EVT_LIBRARY",
    "EVT_LOG",
    "EVT_PHASE",
    "EVT_SHOW",
    "EVT_SOURCE_END",
    "EVT_SOURCE_START",
    "EVT_STARTED",
    "EventEmitter",
    "SyncEvent",
    "SyncEventBroker",
    "make_emitter",
)
