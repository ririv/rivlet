"""Exercise the awaitable entry point from ordinary synchronous tests."""

import asyncio
from collections.abc import Callable

from rivlet import Execution, ExecutionState, GraphMode, GraphObserver, arun

type Invoke = Callable[..., ExecutionState]


def run_async_execution(
    execution: Execution,
    *,
    max_workers: int = 1,
    observer: GraphObserver | None = None,
    mode: GraphMode = "dynamic",
) -> ExecutionState:
    return asyncio.run(
        arun(execution, max_workers=max_workers, observer=observer, mode=mode)
    )
