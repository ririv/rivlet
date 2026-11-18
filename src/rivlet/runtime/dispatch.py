"""Host-side queues for Ready actions, without creating a Future per candidate."""

from __future__ import annotations

from collections import deque
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..admission import Source
    from .session_tree import PythonWork


@dataclass(eq=False, slots=True)
class _Queue:
    source: Source[object] | None
    synchronous: deque[PythonWork] = field(default_factory=deque)
    asynchronous: deque[PythonWork] = field(default_factory=deque)
    subscription: _Subscription | None = None


@dataclass(eq=False, frozen=True, slots=True)
class SourceReady:
    queue: _Queue


@dataclass(frozen=True, slots=True)
class _Subscription:
    signal: SourceReady
    unregister: Callable[[], None]


class Candidates:
    def __init__(self) -> None:
        self._queues: dict[int, _Queue] = {}
        self._ready: deque[_Queue] = deque()
        self._queued: set[_Queue] = set()
        self._waiting_for_submit: dict[_Queue, None] = {}
        self._watching: set[_Queue] = set()

    @property
    def watching(self) -> bool:
        return bool(self._watching)

    def _wake(self, queue: _Queue) -> None:
        if queue.subscription is None and queue not in self._queued:
            self._queued.add(queue)
            self._ready.append(queue)

    def add(self, work: PythonWork) -> None:
        source = work.session.sources.get(work.node)
        key = id(source)
        queue = self._queues.get(key)
        if queue is None:
            queue = _Queue(source)
            self._queues[key] = queue
        if work.node in work.session.definition.async_nodes:
            queue.asynchronous.append(work)
            self._wake(queue)
        else:
            queue.synchronous.append(work)
            if queue not in self._waiting_for_submit:
                self._wake(queue)

    def next(self, *, can_submit: bool) -> tuple[_Queue, PythonWork] | None:
        while self._ready:
            queue = self._ready.popleft()
            self._queued.remove(queue)
            if queue.asynchronous:
                return queue, queue.asynchronous.popleft()
            if queue.synchronous:
                if can_submit:
                    self._waiting_for_submit.pop(queue, None)
                    return queue, queue.synchronous.popleft()
                self._waiting_for_submit[queue] = None
        return None

    def continue_queue(self, queue: _Queue) -> None:
        if queue.asynchronous or queue.synchronous:
            self._wake(queue)

    def postpone(
        self, queue: _Queue, work: PythonWork, notify: Callable[[SourceReady], None]
    ) -> None:
        if queue.source is None:
            raise RuntimeError("cannot watch a local dispatch queue")
        if work.node in work.session.definition.async_nodes:
            queue.asynchronous.appendleft(work)
        else:
            queue.synchronous.appendleft(work)
        signal = SourceReady(queue)
        queue.subscription = _Subscription(
            signal, queue.source.watch(lambda: notify(signal))
        )
        self._watching.add(queue)

    def resource_ready(self, signal: SourceReady) -> None:
        queue = signal.queue
        subscription = queue.subscription
        if subscription is not None and subscription.signal is signal:
            queue.subscription = None
            self._watching.remove(queue)
            subscription.unregister()
            self._wake(queue)

    def submission_available(self) -> None:
        for queue in self._waiting_for_submit:
            self._wake(queue)
        self._waiting_for_submit.clear()

    def close(self) -> None:
        with ExitStack() as cleanup:
            for queue in self._watching:
                if queue.subscription is not None:
                    cleanup.callback(queue.subscription.unregister)
                    queue.subscription = None
            self._watching.clear()
