from __future__ import annotations

import asyncio
from concurrent.futures import (
    CancelledError,
    Future,
    ThreadPoolExecutor,
)
from contextvars import copy_context
from dataclasses import dataclass
from queue import Empty, SimpleQueue
from threading import Lock
from typing import TYPE_CHECKING

from ..events import EventDeliveryError
from .control import ExecutionStopped
from .session_tree import (
    DynamicSubgraph,
    PythonWork,
    SessionDefinition,
    SessionRun,
    SessionTree,
    StepInvocation,
    StepOutcome,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable


@dataclass(frozen=True, slots=True)
class _RustWait:
    session: SessionRun


@dataclass(frozen=True, slots=True)
class _BlockingDispatch:
    pool: ThreadPoolExecutor


@dataclass(frozen=True, slots=True)
class _AsyncDispatch:
    pool: ThreadPoolExecutor
    loop: asyncio.AbstractEventLoop


type _Dispatch = _BlockingDispatch | _AsyncDispatch
type _Deferred = tuple[PythonWork, Awaitable[StepOutcome]]
type _Pending = Future[StepInvocation] | asyncio.Future[StepInvocation]


async def _await_outcome(value: Awaitable[StepOutcome]) -> StepInvocation:
    return await value


async def _invoke_async(work: PythonWork) -> StepInvocation:
    result = work.session.definition.host.run_step(work.node)
    if result is None or isinstance(result, DynamicSubgraph):
        return result
    return await result


class _Driver:
    """Share scheduling and completion handling between blocking and awaitable entry points."""

    def __init__(self, definition: SessionDefinition) -> None:
        self.tree = SessionTree(definition)
        self.pending: dict[_Pending, PythonWork | _RustWait] = {}
        self.rust_waiters: set[SessionRun] = set()
        self.deferred: list[_Deferred] = []
        self.awaiting: list[PythonWork] = []
        self.completed: SimpleQueue[_Pending] = SimpleQueue()
        self._notify_ready: Callable[[_Pending], None] | None = None
        self._notification_lock = Lock()

    def set_notifier(self, notify: Callable[[_Pending], None] | None) -> None:
        with self._notification_lock:
            self._notify_ready = notify

    def _notify(self, future: _Pending) -> None:
        # 必须先入队再读取唤醒器；同步入口切入事件循环时，旧的完成记录
        # 仍在同一个队列里，不会因更换等待方式丢失通知。
        self.completed.put(future)
        # 收尾注销也持同一把锁，确保旧通知不会在事件循环关闭之后才投递。
        with self._notification_lock:
            notify = self._notify_ready
            if notify is not None:
                notify(future)

    def track(self, future: _Pending, item: PythonWork | _RustWait) -> None:
        # 每个 Future 只注册一次通知。A 次提交、C 次完成共 O(A + C) 次
        # 队列操作，不在每次完成后重扫全部 R 个在途 Future。
        self.pending[future] = item
        future.add_done_callback(self._notify)

    def take_completed(self) -> list[_Pending]:
        done: list[_Pending] = []
        while True:
            try:
                done.append(self.completed.get_nowait())
            except Empty:
                return done

    def submit(self, dispatch: _Dispatch) -> None:
        for work in self.tree.poll():
            future: _Pending
            if work.node in work.session.definition.async_nodes:
                match dispatch:
                    case _AsyncDispatch(loop=loop):
                        future = loop.create_task(_invoke_async(work))
                    case _BlockingDispatch():
                        # 交给惰性创建的事件循环，不占线程池，也不为协程
                        # 构造一层 concurrent.futures.Future。
                        self.awaiting.append(work)
                        continue
            else:
                future = dispatch.pool.submit(
                    copy_context().run, work.session.definition.host.run_step, work.node
                )
            self.track(future, work)
        for run in self.tree.rust_runs:
            if run not in self.rust_waiters:
                notification: Future[StepInvocation] = Future()
                run.engine.notify_rust(
                    lambda future=notification: future.set_result(None)
                )
                self.rust_waiters.add(run)
                self.track(notification, _RustWait(run))

    def consume(self, done: Iterable[_Pending]) -> None:
        for future in done:
            item = self.pending.pop(future)
            if isinstance(item, _RustWait):
                future.result()
                self.rust_waiters.remove(item.session)
                self.tree.wake(item.session)
                continue
            try:
                result = future.result()
            except (CancelledError, asyncio.CancelledError) as error:
                raise ExecutionStopped("graph action was cancelled") from error
            except (ExecutionStopped, EventDeliveryError):
                raise
            except Exception as error:
                self.tree.fail(item, error)
            else:
                if result is None or isinstance(result, DynamicSubgraph):
                    self.tree.complete(item, result)
                else:
                    self.deferred.append((item, result))

    def resume(self, loop: asyncio.AbstractEventLoop) -> None:
        for work, value in self.deferred:
            self.track(loop.create_task(_await_outcome(value)), work)
        self.deferred.clear()
        for work in self.awaiting:
            self.track(loop.create_task(_invoke_async(work)), work)
        self.awaiting.clear()

    @property
    def finished(self) -> bool:
        return self.tree.finished and not self.pending

    def require_pending(self) -> None:
        if not self.pending:
            raise RuntimeError("running graph session returned no action")


async def _settle(future: _Pending) -> None:
    result = (
        await asyncio.wrap_future(future)
        if isinstance(future, Future)
        else await future
    )
    if result is not None and not isinstance(result, DynamicSubgraph):
        await result


async def _drain(driver: _Driver) -> None:
    # 不取消包装 Future，也不提前归还资源：借入的池可能仍在执行同步动作。
    pending = asyncio.gather(
        *(_settle(future) for future in driver.pending),
        *(_await_outcome(value) for _, value in driver.deferred),
        return_exceptions=True,
    )
    while not pending.done():
        try:
            await asyncio.shield(pending)
        except asyncio.CancelledError:
            driver.tree.host.request_stop()
    driver.pending.clear()
    driver.deferred.clear()
    driver.take_completed()


async def _continue_async(
    driver: _Driver,
    pool: ThreadPoolExecutor,
) -> None:
    loop = asyncio.get_running_loop()
    dispatch = _AsyncDispatch(pool, loop)
    driver.resume(loop)
    ready = asyncio.Event()

    def notify(future: _Pending) -> None:
        if isinstance(future, asyncio.Future):
            ready.set()
        else:
            loop.call_soon_threadsafe(ready.set)

    driver.set_notifier(notify)

    try:
        while True:
            driver.submit(dispatch)
            if driver.finished:
                break
            driver.require_pending()
            # clear 在读队列之前：两者之间到达的完成记录要么被本轮取走，
            # 要么保留 set 通知供下一轮等待，不丢失线程侧的唤醒。
            ready.clear()
            done = driver.take_completed()
            if not done:
                await ready.wait()
                done = driver.take_completed()
            driver.consume(done)
            driver.resume(loop)
        driver.tree.host.require_running()
    except BaseException:
        driver.tree.host.request_stop()
        raise
    finally:
        await _drain(driver)
        driver.set_notifier(None)
    driver.tree.host.raise_failures()


def run_session(definition: SessionDefinition, pool: ThreadPoolExecutor) -> None:
    driver = _Driver(definition)
    dispatch = _BlockingDispatch(pool)
    try:
        while True:
            driver.submit(dispatch)
            if driver.finished:
                break
            if driver.awaiting:
                break
            driver.require_pending()
            done = [driver.completed.get(), *driver.take_completed()]
            driver.consume(done)
            if driver.deferred:
                # 只在实际遇到 awaitable 时创建一次循环；沿用当前 driver、
                # session、线程池和未完成工作，不重新编译或重跑已完成动作。
                break
    except BaseException:
        driver.tree.host.request_stop()
        # 同步函数也可能刚返回协程，必须等待已有动作收尾而不是丢弃它。
        for future, item in driver.pending.items():
            try:
                result = future.result()
            except BaseException:
                continue
            if (
                isinstance(item, PythonWork)
                and result is not None
                and not isinstance(result, DynamicSubgraph)
            ):
                driver.deferred.append((item, result))
        driver.pending.clear()
        if driver.deferred:
            with asyncio.Runner() as runner:
                runner.run(_drain(driver))
        raise
    if driver.deferred or driver.awaiting:
        with asyncio.Runner() as runner:
            runner.run(_continue_async(driver, pool))
    else:
        driver.tree.host.require_running()
        driver.tree.host.raise_failures()


async def arun_session(definition: SessionDefinition, pool: ThreadPoolExecutor) -> None:
    await _continue_async(_Driver(definition), pool)
