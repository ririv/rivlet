"""Validate the shared-interpreter resource required by Python action execution."""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor

if sys.version_info >= (3, 14):
    from concurrent.futures import InterpreterPoolExecutor


def require_shared_thread_pool(pool: ThreadPoolExecutor) -> None:
    if not isinstance(pool, ThreadPoolExecutor):
        raise TypeError(
            "graph execution requires a same-interpreter ThreadPoolExecutor"
        )
    # InterpreterPoolExecutor 继承线程池，但不能共享闭包、上下文或扩展对象。
    if sys.version_info >= (3, 14) and isinstance(pool, InterpreterPoolExecutor):
        raise TypeError("graph execution does not support isolated interpreter pools")
