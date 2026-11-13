from __future__ import annotations

import asyncio
import inspect
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..events import EventDeliveryError
from .control import ExecutionStopped
from .session_tree import (
    BatchExecutionHost,
    DynamicSubgraph,
    LeafOutcome,
    PythonWork,
    SessionDefinition,
    SessionRun,
    SessionTree,
)

if TYPE_CHECKING:
    from .._native import CompiledExecutionGraph, RustActionBindings


@dataclass(frozen=True, slots=True)
class _RustWait:
    session: SessionRun


def _wait_rust(run: SessionRun) -> LeafOutcome:
    run.engine.wait_rust()
    return None


async def _invoke_async(work: PythonWork) -> LeafOutcome:
    result = work.session.definition.host.run_leaf(work.node)
    if inspect.isawaitable(result):
        return await result
    return result


def _invoke_sync(work: PythonWork) -> LeafOutcome:
    result = work.session.definition.host.run_leaf(work.node)
    if result is None or isinstance(result, DynamicSubgraph):
        return result
    raise TypeError("synchronous driver received an asynchronous action host")


def run_serial_session(
    graph: CompiledExecutionGraph,
    host: BatchExecutionHost,
    rust_actions: RustActionBindings,
    rust_contexts: list[tuple[int, str | None]],
) -> None:
    from .serial_session import run_serial_definition

    run_serial_definition(SessionDefinition(graph, host, rust_actions, rust_contexts))


def run_threaded_session(
    graph: CompiledExecutionGraph,
    host: BatchExecutionHost,
    *,
    workers: int,
    rust_actions: RustActionBindings,
    rust_contexts: list[tuple[int, str | None]],
) -> None:
    tree = SessionTree(
        SessionDefinition(graph, host, rust_actions, rust_contexts), workers
    )
    running: dict[Future[LeafOutcome], PythonWork | _RustWait] = {}
    rust_waiters: set[SessionRun] = set()
    with ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="GraphWorker"
    ) as pool:
        try:
            while True:
                for work in tree.poll():
                    running[pool.submit(_invoke_sync, work)] = work
                for run in tree.rust_runs:
                    if run not in rust_waiters:
                        rust_waiters.add(run)
                        running[pool.submit(_wait_rust, run)] = _RustWait(run)
                if tree.finished and not running:
                    break
                if not running:
                    raise RuntimeError(
                        "running graph session returned no threaded action"
                    )
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in done:
                    item = running.pop(future)
                    if isinstance(item, _RustWait):
                        future.result()
                        rust_waiters.remove(item.session)
                        tree.wake(item.session)
                        continue
                    try:
                        outcome = future.result()
                    except (ExecutionStopped, EventDeliveryError):
                        raise
                    except Exception as error:
                        tree.fail(item, error)
                    else:
                        tree.complete(item, outcome)
        except BaseException:
            host.request_stop()
            raise
    host.require_running()
    host.raise_failures()


async def run_async_session(
    graph: CompiledExecutionGraph,
    host: BatchExecutionHost,
    *,
    concurrency: int,
    rust_actions: RustActionBindings,
    rust_contexts: list[tuple[int, str | None]],
) -> None:
    tree = SessionTree(
        SessionDefinition(graph, host, rust_actions, rust_contexts), concurrency
    )
    running: dict[asyncio.Future[LeafOutcome], PythonWork | _RustWait] = {}
    rust_waiters: set[SessionRun] = set()
    try:
        while True:
            for work in tree.poll():
                running[asyncio.create_task(_invoke_async(work))] = work
            for run in tree.rust_runs:
                if run not in rust_waiters:
                    rust_waiters.add(run)
                    running[asyncio.create_task(asyncio.to_thread(_wait_rust, run))] = (
                        _RustWait(run)
                    )
            if tree.finished and not running:
                break
            if not running:
                raise RuntimeError("running graph session returned no async action")
            done, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                item = running.pop(task)
                if isinstance(item, _RustWait):
                    task.result()
                    rust_waiters.remove(item.session)
                    tree.wake(item.session)
                    continue
                try:
                    outcome = task.result()
                except (ExecutionStopped, EventDeliveryError):
                    raise
                except Exception as error:
                    tree.fail(item, error)
                else:
                    tree.complete(item, outcome)
        host.require_running()
    except BaseException:
        host.request_stop()
        raise
    finally:
        # 等待已启动工作退出；外层取消不会直接取消仍在操作资源的 action。
        pending = asyncio.gather(*running, return_exceptions=True)
        while not pending.done():
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                host.request_stop()
    host.raise_failures()
