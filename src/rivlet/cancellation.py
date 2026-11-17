"""Explicit cancellation authority and read-only cooperative action signals."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from threading import Event, Lock, RLock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from .runtime.state import ExecutionState


class CancellationRequested(Exception):
    """An action acknowledged cancellation of its own execution token."""

    def __init__(self, token: CancellationToken) -> None:
        super().__init__("execution cancellation requested")
        self.token = token


class ExecutionCancelled(Exception):
    """Execution has drained in-flight work and retained its partial state."""

    def __init__(self, state: ExecutionState) -> None:
        super().__init__("graph execution cancelled")
        self.state = state


@dataclass(slots=True)
class _CancellationState:
    event: Event = field(default_factory=Event)
    lock: Lock = field(default_factory=Lock)
    handlers: dict[object, Callable[[], None]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CancellationToken:
    """Observe and await one cancellation request without authority to cancel."""

    _state: _CancellationState = field(default_factory=_CancellationState, repr=False)

    def is_cancelled(self) -> bool:
        return self._state.event.is_set()

    def require_running(self) -> None:
        if self.is_cancelled():
            raise CancellationRequested(self)

    def _subscribe(self, handler: Callable[[], None]) -> Callable[[], None]:
        key = object()
        active = True
        delivery = RLock()

        def notify() -> None:
            with delivery:
                if active:
                    handler()

        with self._state.lock:
            cancelled = self.is_cancelled()
            if not cancelled:
                self._state.handlers[key] = notify
        if cancelled:
            notify()

        def unregister() -> None:
            nonlocal active
            # 等已开始的回调结束；注销返回后不能再向已关闭的事件循环投递。
            with delivery:
                active = False
            with self._state.lock:
                self._state.handlers.pop(key, None)

        return unregister

    async def wait_cancelled(self) -> None:
        loop = asyncio.get_running_loop()
        ready = asyncio.Event()

        def notify() -> None:
            loop.call_soon_threadsafe(ready.set)

        unregister = self._subscribe(notify)
        try:
            await ready.wait()
        finally:
            unregister()


class CancellationSource:
    """Own the authority to cancel one or more explicitly associated executions."""

    def __init__(self) -> None:
        self._token = CancellationToken()

    @property
    def token(self) -> CancellationToken:
        return self._token

    def cancel(self) -> None:
        state = self._token._state
        with state.lock:
            if state.event.is_set():
                return
            state.event.set()
            handlers = tuple(state.handlers.values())
            state.handlers.clear()
        errors: list[Exception] = []
        for handler in handlers:
            try:
                handler()
            except Exception as error:
                errors.append(error)
        if errors:
            raise ExceptionGroup("cancellation notification failed", errors)
