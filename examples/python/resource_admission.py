"""Let an independent action run while another waits for an external slot."""

from __future__ import annotations

from threading import Event, Lock
from typing import TYPE_CHECKING

from rivlet import Graph, Lease, Limit, Step, run

if TYPE_CHECKING:
    from collections.abc import Callable

    from rivlet import NodeRunContext


class SingleSlot:
    """A small in-memory Source, standing in for a jobserver adapter."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._taken = False
        self._watchers: dict[object, Callable[[], None]] = {}

    def try_acquire(self) -> Lease[str] | None:
        with self._lock:
            if self._taken:
                return None
            self._taken = True
        return Lease("build slot", self._release)

    def watch(self, wake: Callable[[], None]) -> Callable[[], None]:
        key = object()
        with self._lock:
            already_ready = not self._taken
            if not already_ready:
                self._watchers[key] = wake
        if already_ready:
            wake()

        def unregister() -> None:
            with self._lock:
                self._watchers.pop(key, None)

        return unregister

    def _release(self) -> None:
        with self._lock:
            self._taken = False
            watchers = tuple(self._watchers.values())
            self._watchers.clear()
        for wake in watchers:
            wake()


def main() -> None:
    slots = SingleSlot()
    building, checked = Event(), Event()

    def build_a(ctx: NodeRunContext) -> None:
        print(f"A: acquired {ctx.permit(slots)}")
        building.set()
        assert checked.wait(5)
        print("A: finished")

    def build_b(ctx: NodeRunContext) -> None:
        assert checked.is_set()
        print(f"B: acquired {ctx.permit(slots)}")

    def check_cache() -> None:
        # 仅用于演示顺序：让 C 在 A 仍持有名额时完成。
        assert building.wait(5)
        print("C: cache check ran before B could acquire a slot")
        checked.set()

    a = Step(build_a)
    b = Step(build_b)
    c = Step(check_cache)
    workflow = Graph(nodes=(a, b, c))
    run(
        workflow,
        max_workers=2,
        max_concurrency={
            workflow: 2,
            a: Limit(1, source=slots),
            b: Limit(1, source=slots),
        },
    )


if __name__ == "__main__":
    main()
