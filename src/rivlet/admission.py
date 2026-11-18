"""Execution-time admission contracts for externally shared action slots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, Protocol, TypeVar

from .errors import GraphError

if TYPE_CHECKING:
    from collections.abc import Callable

_T_co = TypeVar("_T_co", covariant=True)


@dataclass(frozen=True, slots=True)
class Lease(Generic[_T_co]):  # noqa: UP046
    """One acquired slot; only the execution host calls release."""

    value: _T_co
    release: Callable[[], None]


class Source(Protocol[_T_co]):
    """Nonblocking, shareable source of one slot per admitted action."""

    def try_acquire(self) -> Lease[_T_co] | None:
        """Acquire immediately, return None on contention, or raise on failure."""
        ...

    def watch(self, wake: Callable[[], None]) -> Callable[[], None]:
        """Arm one notification and return its unregister function.

        通知可以跨线程或在订阅时发生；资源已可用或已损坏也必须通知，
        避免申请失败至订阅之间的变化丢失。注销必须可与通知竞争。
        """
        ...


@dataclass(frozen=True, slots=True)
class Limit:
    """Bind a local action maximum and optionally an external slot source."""

    maximum: int
    source: Source[object] | None = None

    def __post_init__(self) -> None:
        if type(self.maximum) is not int or self.maximum < 1:
            raise GraphError("concurrency limits must be positive integers")


@dataclass(frozen=True, slots=True)
class _Permit:
    source: Source[object]
    value: object
