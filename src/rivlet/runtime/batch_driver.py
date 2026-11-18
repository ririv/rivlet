from __future__ import annotations

import asyncio
import inspect
from concurrent.futures import (
    CancelledError,
    Future,
    ThreadPoolExecutor,
)
from contextlib import ExitStack
from contextvars import copy_context
from dataclasses import dataclass
from queue import Empty, SimpleQueue
from threading import Lock
from typing import TYPE_CHECKING, cast, overload

from ..admission import _Permit
from ..events import EventDeliveryError
from .control import ExecutionStopped
from .dispatch import Candidates, SourceReady
from .session_tree import (
    ConditionWork,
    DynamicSubgraph,
    PythonWork,
    SessionDefinition,
    SessionTree,
    StepInvocation,
    StepOutcome,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from ..admission import Lease
    from .control import ExecutionControl
    from .state import ExecutionState


@dataclass(frozen=True, slots=True)
class _BlockingDispatch:
    pool: ThreadPoolExecutor


@dataclass(frozen=True, slots=True)
class _AsyncDispatch:
    pool: ThreadPoolExecutor
    loop: asyncio.AbstractEventLoop


type _Dispatch = _BlockingDispatch | _AsyncDispatch
type _Deferred = tuple[PythonWork, Awaitable[StepOutcome]]
type _ActionPending = Future[StepInvocation] | asyncio.Future[StepInvocation]
type _ConditionPending = asyncio.Future[bool]
type _Pending = _ActionPending | _ConditionPending
type _Notice = _Pending | SourceReady | None


async def _await_outcome(value: Awaitable[StepOutcome]) -> StepInvocation:
    return await value


async def _await_condition(value: Awaitable[bool]) -> bool:
    return await value


async def _invoke_async(work: PythonWork, permit: _Permit | None) -> StepInvocation:
    result = work.session.definition.host.run_step(work.node, permit)
    if result is None or isinstance(result, DynamicSubgraph):
        return result
    return await result


class _Driver:
    """Share scheduling and completion handling between blocking and awaitable entry points."""

    def __init__(
        self, definition: SessionDefinition, max_submitted: int | None
    ) -> None:
        self.tree = SessionTree(definition, host_dispatch=max_submitted is not None)
        self.candidates = Candidates() if self.tree.host_dispatch else None
        self.max_submitted = max_submitted
        self.submitted = 0
        self.leases: dict[PythonWork, Lease[object]] = {}
        self.pending: dict[_Pending, PythonWork | ConditionWork] = {}
        self.deferred: list[_Deferred] = []
        self.deferred_conditions: list[tuple[ConditionWork, Awaitable[bool]]] = []
        self.awaiting: list[PythonWork] = []
        self.completed: SimpleQueue[_Notice] = SimpleQueue()
        self._notify_ready: Callable[[_Notice], None] | None = None
        self._notification_lock = Lock()

    def set_notifier(self, notify: Callable[[_Notice], None] | None) -> None:
        with self._notification_lock:
            self._notify_ready = notify

    def _notify(self, future: _Notice) -> None:
        # 必须先入队再读取唤醒器；同步入口切入事件循环时，旧的完成记录
        # 仍在同一个队列里，不会因更换等待方式丢失通知。
        self.completed.put(future)
        # 收尾注销也持同一把锁，确保旧通知不会在事件循环关闭之后才投递。
        with self._notification_lock:
            notify = self._notify_ready
            if notify is not None:
                notify(future)

    def wake(self) -> None:
        self._notify(None)

    @overload
    def track(self, future: _ActionPending, item: PythonWork) -> None: ...

    @overload
    def track(self, future: _ConditionPending, item: ConditionWork) -> None: ...

    def track(self, future: _Pending, item: PythonWork | ConditionWork) -> None:
        # 每个 Future 只注册一次通知。A 次提交、C 次完成共 O(A + C) 次
        # 队列操作，不在每次完成后重扫全部 R 个在途 Future。
        self.pending[future] = item
        future.add_done_callback(self._notify)

    def take_completed(self) -> list[_Notice]:
        done: list[_Notice] = []
        while True:
            try:
                done.append(self.completed.get_nowait())
            except Empty:
                return done

    def submit(self, dispatch: _Dispatch) -> None:
        while True:
            for work in self.tree.poll():
                if isinstance(work, ConditionWork):
                    self._submit_condition(work, dispatch)
                    continue
                if self.candidates is None:
                    self._submit_action(work, dispatch)
                else:
                    self.candidates.add(work)
            if self.candidates is not None and not self.tree.finished:
                self._dispatch_candidates(dispatch, self.candidates)
            # 同步条件直接回传，沿用本次派发循环，不创建 Future 或事件循环。
            if not self.tree.has_ready:
                break

    def _dispatch_candidates(self, dispatch: _Dispatch, candidates: Candidates) -> None:
        while (
            item := candidates.next(
                can_submit=self.max_submitted is None
                or self.submitted < self.max_submitted
            )
        ) is not None:
            queue, work = item
            host = work.session.definition.host
            lease = None
            if (source := queue.source) is not None:
                host.require_running()
                if not work.session.engine.check_dispatch(work.node):
                    candidates.continue_queue(queue)
                    continue
                try:
                    lease = source.try_acquire()
                except Exception as error:
                    self.tree.fail_candidate(work, error)
                    candidates.continue_queue(queue)
                    continue
                if lease is None:
                    candidates.postpone(queue, work, self._notify)
                    continue
            candidates.continue_queue(queue)
            try:
                host.require_running()
                confirmed = self.tree.confirm(work)
                if confirmed and lease is not None:
                    self.leases[work] = lease
                    lease = None
                if confirmed:
                    self._submit_action(work, dispatch)
            finally:
                # 已有外部名额但本地额度不再可用，或取消先发生：不持有部分资源等待。
                if lease is not None:
                    lease.release()

    def _permit(self, work: PythonWork) -> _Permit | None:
        lease = self.leases.get(work)
        return (
            None
            if lease is None
            else _Permit(work.session.sources[work.node], lease.value)
        )

    def _release(self, work: PythonWork) -> None:
        if (lease := self.leases.pop(work, None)) is not None:
            lease.release()

    def _submit_action(self, work: PythonWork, dispatch: _Dispatch) -> None:
        host = work.session.definition.host
        future: _ActionPending
        permit = self._permit(work)
        try:
            if work.node in work.session.definition.async_nodes:
                match dispatch:
                    case _AsyncDispatch(loop=loop):
                        future = loop.create_task(_invoke_async(work, permit))
                    case _BlockingDispatch():
                        # 交给惰性创建的事件循环，不占线程池。
                        self.awaiting.append(work)
                        return
            else:
                future = dispatch.pool.submit(
                    copy_context().run, host.run_step, work.node, permit
                )
                self.submitted += 1
        except Exception as error:
            self._release(work)
            self.tree.fail(work, error)
            return
        self.track(future, work)

    def _submit_condition(self, work: ConditionWork, dispatch: _Dispatch) -> None:
        host = work.session.definition.host
        host.require_running()
        try:
            value = host.evaluate_condition(work.node, work.condition)
        except (ExecutionStopped, EventDeliveryError):
            raise
        except Exception as error:
            self.tree.fail(work, error)
            return
        if isinstance(value, bool):
            self.tree.resolve_condition(work, value)
            return
        match dispatch:
            case _AsyncDispatch(loop=loop):
                self.track(loop.create_task(_await_condition(value)), work)
            case _BlockingDispatch():
                self.deferred_conditions.append((work, value))

    def _consume_condition(
        self, future: _ConditionPending, work: ConditionWork
    ) -> None:
        try:
            value = future.result()
        except (CancelledError, asyncio.CancelledError) as error:
            raise ExecutionStopped("graph condition was cancelled") from error
        except (ExecutionStopped, EventDeliveryError):
            raise
        except Exception as error:
            self.tree.fail(work, error)
        else:
            self.tree.resolve_condition(work, value)

    def consume(self, done: Iterable[_Notice]) -> None:
        for future in done:
            if future is None:
                continue
            if isinstance(future, SourceReady):
                assert self.candidates is not None
                self.candidates.resource_ready(future)
                continue
            item = self.pending.pop(future)
            # track 的重载约束 Future 与工作种类；异构注册表只在此恢复对应类型。
            if isinstance(item, ConditionWork):
                self._consume_condition(cast("_ConditionPending", future), item)
                continue
            if isinstance(future, Future):
                was_full = self.submitted == self.max_submitted
                self.submitted -= 1
                if was_full and self.candidates is not None:
                    self.candidates.submission_available()
            try:
                result = cast("_ActionPending", future).result()
            except (CancelledError, asyncio.CancelledError) as error:
                self._release(item)
                raise ExecutionStopped("graph action was cancelled") from error
            except (ExecutionStopped, EventDeliveryError):
                self._release(item)
                raise
            except Exception as error:
                self._release(item)
                self.tree.fail(item, error)
            else:
                if result is None or isinstance(result, DynamicSubgraph):
                    self._release(item)
                    self.tree.complete(item, result)
                else:
                    self.deferred.append((item, result))

    def resume(self, loop: asyncio.AbstractEventLoop) -> None:
        for work, value in self.deferred:
            self.track(loop.create_task(_await_outcome(value)), work)
        self.deferred.clear()
        for work, value in self.deferred_conditions:
            self.track(loop.create_task(_await_condition(value)), work)
        self.deferred_conditions.clear()
        for work in self.awaiting:
            self.track(loop.create_task(_invoke_async(work, self._permit(work))), work)
        self.awaiting.clear()

    @property
    def finished(self) -> bool:
        return self.tree.finished and not self.pending

    def require_pending(self) -> None:
        if not self.pending and not (self.candidates and self.candidates.watching):
            raise RuntimeError("running graph session returned no action")

    def close(self) -> None:
        # 即使某个适配器在清理时抛错，也要尝试归还其它已取得的租约。
        with ExitStack() as cleanup:
            for work in tuple(self.leases):
                cleanup.callback(self._release, work)
            if self.candidates is not None:
                cleanup.callback(self.candidates.close)


async def _settle(future: _Pending) -> None:
    result = (
        await asyncio.wrap_future(future)
        if isinstance(future, Future)
        else await future
    )
    if inspect.isawaitable(result):
        await result


async def _drain(driver: _Driver) -> None:
    # 不取消包装 Future，也不提前归还资源：借入的池可能仍在执行同步动作。
    pending = asyncio.gather(
        *(_settle(future) for future in driver.pending),
        *(_await_outcome(value) for _, value in driver.deferred),
        *(_await_condition(value) for _, value in driver.deferred_conditions),
        return_exceptions=True,
    )
    while not pending.done():
        try:
            await asyncio.shield(pending)
        except asyncio.CancelledError:
            driver.tree.host.request_stop()
    driver.pending.clear()
    driver.deferred.clear()
    driver.deferred_conditions.clear()
    driver.take_completed()


async def _continue_async(
    driver: _Driver,
    pool: ThreadPoolExecutor,
) -> None:
    loop = asyncio.get_running_loop()
    dispatch = _AsyncDispatch(pool, loop)
    ready = asyncio.Event()

    def notify(future: _Notice) -> None:
        if isinstance(future, asyncio.Future):
            ready.set()
        else:
            loop.call_soon_threadsafe(ready.set)

    driver.set_notifier(notify)

    try:
        driver.resume(loop)
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
        try:
            await _drain(driver)
        finally:
            driver.set_notifier(None)
    driver.tree.host.raise_failures()


def _drain_blocking(driver: _Driver) -> None:
    # 同步函数也可能刚返回协程；等待本次提交的动作真正结束再归还资源。
    for future, item in driver.pending.items():
        while True:
            try:
                result = cast("_ActionPending", future).result()
            except BaseException:
                if not future.done():
                    continue
            else:
                if (
                    isinstance(item, PythonWork)
                    and result is not None
                    and not isinstance(result, DynamicSubgraph)
                ):
                    driver.deferred.append((item, result))
            break
    driver.pending.clear()
    if driver.deferred or driver.deferred_conditions:
        with asyncio.Runner() as runner:
            runner.run(_drain(driver))


def _run_blocking(driver: _Driver, pool: ThreadPoolExecutor) -> None:
    dispatch = _BlockingDispatch(pool)
    try:
        while True:
            driver.submit(dispatch)
            if driver.finished:
                break
            if driver.awaiting or driver.deferred_conditions:
                break
            driver.require_pending()
            done = [driver.completed.get(), *driver.take_completed()]
            driver.consume(done)
            if driver.deferred:
                # 只在实际遇到 awaitable 时创建一次循环；沿用当前 driver、
                # session、线程池和未完成工作，不重新编译或重跑已完成动作。
                break
    except BaseException:
        try:
            driver.tree.host.request_stop()
        finally:
            _drain_blocking(driver)
        raise
    if driver.deferred or driver.awaiting or driver.deferred_conditions:
        with asyncio.Runner() as runner:
            runner.run(_continue_async(driver, pool))
    else:
        driver.tree.host.require_running()
        driver.tree.host.raise_failures()


def run_session(
    definition: SessionDefinition,
    pool: ThreadPoolExecutor,
    control: ExecutionControl,
    state: ExecutionState,
    max_submitted: int | None,
) -> None:
    driver = _Driver(definition, max_submitted)
    with _driver_scope(driver, control, state):
        _run_blocking(driver, pool)


async def arun_session(
    definition: SessionDefinition,
    pool: ThreadPoolExecutor,
    control: ExecutionControl,
    state: ExecutionState,
    max_submitted: int | None,
) -> None:
    driver = _Driver(definition, max_submitted)
    with _driver_scope(driver, control, state):
        await _continue_async(driver, pool)


def _driver_scope(
    driver: _Driver, control: ExecutionControl, state: ExecutionState
) -> ExitStack:
    # 等待外部资源时可能没有任何 Future；停止和动态跳过都要直接唤醒 driver。
    with ExitStack() as scope:
        scope.callback(driver.close)
        control.register_stop_handler(driver.wake)
        scope.callback(control.unregister_stop_handler, driver.wake)
        if driver.tree.has_sources:
            state._set_skip_wake(driver.wake)
            scope.callback(state._set_skip_wake, None)
        return scope.pop_all()
