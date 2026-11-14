"""测量阻塞与可等待入口的动态子图耗时，以及同步动作的线程并发。"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import threading
import time
from dataclasses import dataclass, field

from rivlet import (
    Graph,
    GraphStructure,
    Step,
    arun,
    run,
)


@dataclass
class Activity:
    active: int = 0
    peak: int = 0
    completed: int = 0
    delay: float = 0.001
    lock: threading.Lock = field(default_factory=threading.Lock)

    def work(self) -> None:
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            if self.delay:
                time.sleep(self.delay)
        finally:
            with self.lock:
                self.active -= 1
                self.completed += 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--implementation", default="current")
    parser.add_argument("--entry", choices=("run", "arun"), default="run")
    parser.add_argument("--width", type=int, default=3)
    parser.add_argument("--depth", type=int, default=3)
    parser.add_argument("--leaves", type=int, default=4)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--delay", type=float, default=0.001)
    parser.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args()
    if (
        min(args.width, args.leaves, args.workers, args.repeats) < 1
        or args.depth < 0
        or args.delay < 0
    ):
        parser.error(
            "width, leaves, workers, repeats must be positive; depth and delay must be nonnegative"
        )
    samples: list[float] = []
    peaks: list[int] = []
    expected = args.width**args.depth * args.leaves
    for repeat in range(args.repeats + 1):
        activity = Activity(delay=args.delay)

        def branch(depth: int, activity: Activity = activity) -> Graph:
            if depth == 0:
                return Graph(
                    structure=GraphStructure(
                        nodes=tuple(Step(activity.work) for _ in range(args.leaves))
                    )
                )
            return Graph(
                structure=GraphStructure(
                    nodes=tuple(
                        Step(lambda: branch(depth - 1)) for _ in range(args.width)
                    )
                )
            )

        graph = branch(args.depth)
        start = time.perf_counter()
        if args.entry == "arun":
            asyncio.run(arun(graph, max_workers=args.workers))
        else:
            run(graph, max_workers=args.workers)
        elapsed = time.perf_counter() - start
        if activity.completed != expected:
            raise RuntimeError(
                f"expected {expected} completed actions, got {activity.completed}"
            )
        if repeat:
            samples.append(elapsed)
            peaks.append(activity.peak)
    print(
        json.dumps(
            {
                "implementation": args.implementation,
                "entry": args.entry,
                "width": args.width,
                "depth": args.depth,
                "leaves": expected,
                "workers": args.workers,
                "delay": args.delay,
                "median_seconds": statistics.median(samples),
                "samples_seconds": samples,
                "peak_actions": peaks,
            }
        )
    )


if __name__ == "__main__":
    main()
