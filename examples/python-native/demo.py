"""Experimental: compose independently compiled Rust steps with Python steps."""

from demo_native import pipeline

from rivlet import Edge, Graph, NodeRunContext, Option, Step, run


def show(_ctx: NodeRunContext, value: Option[int]) -> None:
    print("Python received:", value.unwrap())


prepare = Step(lambda: 21, label="prepare")
native = pipeline
display = Step(show, label="display")
workflow = Graph(
    nodes=(prepare, native, display),
    edges=(
        *Edge.results((prepare,), native.interface.input),
        *Edge.results((native.interface.output,), display),
    ),
    label=("mixed"),
)

if __name__ == "__main__":
    run(workflow, max_workers=2)
