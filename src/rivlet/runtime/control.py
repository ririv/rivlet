from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from ..cancellation import CancellationSource, CancellationToken

type StopHandler = Callable[[], None]


class ExecutionStopped(RuntimeError):
    """Graph 执行作用域已经收到停止请求。"""


@dataclass(slots=True)
class ExecutionControl:
    """协调一组 Graph 执行及其宿主资源的停止状态。

    `Execution` 默认创建独享实例；宿主若把多个顺序 Execution 视为同一次请求，
    可以显式传入同一实例。执行结果仍分别保存在各 Execution 的
    `ExecutionState` 中，不进入本对象。
    """

    cancellation: CancellationToken = field(default_factory=CancellationToken)
    _stop_event: threading.Event = field(
        default_factory=threading.Event,
        init=False,
        repr=False,
    )
    _handler_lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
    )
    _stop_handlers: list[StopHandler] = field(
        default_factory=list,
        init=False,
        repr=False,
    )

    def stop_requested(self) -> bool:
        return self._stop_event.is_set() or self.cancellation.is_cancelled()

    def require_running(self) -> None:
        self.cancellation.require_running()
        if self._stop_event.is_set():
            raise ExecutionStopped("graph execution stop requested")

    async def wait_cancelled(self) -> None:
        # 合并两种通知，不把宿主中断写回调用方可能共享的取消来源。
        wake = CancellationSource()
        unregister = self.cancellation._subscribe(wake.cancel)
        handler = wake.cancel
        self.register_stop_handler(handler)
        try:
            await wake.token.wait_cancelled()
        finally:
            unregister()
            with self._handler_lock:
                self._stop_handlers.remove(handler)

    def require_not_stopped(self) -> None:
        if self._stop_event.is_set():
            raise ExecutionStopped("graph execution stop requested")

    def register_stop_handler(self, handler: StopHandler) -> None:
        with self._handler_lock:
            stopped = self._stop_event.is_set()
            if handler in self._stop_handlers:
                return
            self._stop_handlers.append(handler)
        if stopped:
            handler()

    def request_stop(self) -> None:
        with self._handler_lock:
            if self._stop_event.is_set():
                return
            self._stop_event.set()
            handlers = tuple(self._stop_handlers)
        failures: list[Exception] = []
        for handler in handlers:
            try:
                handler()
            except Exception as error:
                failures.append(error)
        if failures:
            raise ExceptionGroup("graph stop handlers failed", failures)
