"""Validate orthogonal port routing and execution conditions."""

from __future__ import annotations

from threading import Event

import pytest

from rivlet import (
    AsyncRunOptions,
    Edge,
    Execution,
    Graph,
    GraphError,
    GraphInterface,
    GraphStructure,
    Leaf,
    StaticCondition,
    StaticConditions,
    ThreadRunOptions,
    export_graph,
)


def require(event: Event) -> None:
    assert event.wait(2), "an independent graph branch was unexpectedly blocked"


def test_same_name_shortcut_and_pair_are_the_same_declaration():
    a, b = Leaf(lambda: None), Leaf(lambda: None)
    assert Edge(a, b, ports="ready") == Edge(a, b, ports=("ready", "ready"))
    assert Edge(a, b).ports is None


@pytest.mark.parametrize("mode", ("compiled", "dynamic"))
@pytest.mark.parametrize("asynchronous", (False, True))
@pytest.mark.parametrize("enabled", (False, True))
@pytest.mark.parametrize("project", (False, True))
def test_conditional_named_ports_gate_only_internal_consumers(
    mode, asynchronous, enabled, project
):
    prepared, supplied, used_a, used_b = (Event() for _ in range(4))
    calls = []

    def provide():
        require(prepared)
        if not enabled:
            # 条件为假时，消费步骤必须能先运行，不能仍然等待这个出口。
            require(used_a)
            require(used_b)
        supplied.set()

    def finish():
        require(used_a)
        require(used_b)

    def use(event):
        if enabled:
            assert supplied.is_set()
        event.set()

    ready = Leaf(provide, label=("ready"))
    provider = (ready >> Leaf(finish, label=("tail"))).graph(
        interface=GraphInterface(outputs={"sdk-ready": ready}), label=("provider")
    )
    first, second = Leaf(lambda: use(used_a)), Leaf(lambda: use(used_b))
    consumer = (Leaf(prepared.set) >> (first | second)).graph(
        interface=GraphInterface(inputs={"toolchain-ready": (first, second)}),
        label=("consumer"),
    )

    def decide():
        # 条件的结果输入不能把整个 consumer 挡住，否则 prepare 永远无法运行。
        require(prepared)
        return enabled

    probe = Leaf(decide, label=("probe"))

    def predicate(result):
        calls.append(result.unwrap())
        return result.unwrap() is True

    condition = predicate
    unused = Leaf(lambda: pytest.fail("static exclusion did not apply"))
    graph = Graph(
        structure=GraphStructure(
            nodes=(unused, probe, provider, consumer)
            if project
            else (probe, provider, consumer),
            edges=(
                Edge(
                    provider,
                    consumer,
                    ports=("sdk-ready", "toolchain-ready"),
                    condition=(condition, probe),
                ),
            ),
            static=StaticConditions(
                evaluator=lambda item: bool(item.payload),
                conditions={
                    unused: StaticCondition(payload=False, description="disabled")
                },
            )
            if project
            else (),
        )
    )
    execution = Execution(Graph(structure=graph.structure))
    execution.run(
        AsyncRunOptions(concurrency=4) if asynchronous else ThreadRunOptions(workers=4),
        mode=mode,
    )
    assert used_a.is_set() and used_b.is_set()
    assert calls == [enabled]
    document = export_graph(execution.compile())
    edge = next(edge for edge in document["edges"] if edge["kind"] == "port")
    assert edge["source_port"] == "sdk-ready"
    assert edge["target_port"] == "toolchain-ready"
    assert edge["condition"] is not None


def test_port_directions_are_fixed_by_tuple_position():
    ready, use = Leaf(lambda: None), Leaf(lambda: None)
    a = Graph(
        structure=GraphStructure(
            nodes=(ready,), interface=GraphInterface(outputs={"out": ready})
        )
    )
    b = Graph(
        structure=GraphStructure(
            nodes=(use,), interface=GraphInterface(inputs={"in": use})
        )
    )
    Graph(
        structure=GraphStructure(nodes=(a, b), edges=(Edge(a, b, ports=("out", "in")),))
    )
    # 任意名称来自用户声明，无法通过 Python 类型预知；必须校验真实图接口。
    with pytest.raises(GraphError, match="does not provide port in"):
        Graph(
            structure=GraphStructure(
                nodes=(a, b), edges=(Edge(a, b, ports=("in", "out")),)
            )
        )


@pytest.mark.parametrize("enabled", (False, True))
@pytest.mark.parametrize("mode", ("compiled", "dynamic"))
def test_port_condition_controls_failure_propagation(enabled, mode):
    calls = []

    def fail():
        raise RuntimeError("provider failed")

    output = Leaf(fail)
    provider = Graph(
        structure=GraphStructure(
            nodes=(output,), interface=GraphInterface(outputs={"out": output})
        )
    )
    prepare = Leaf(lambda: calls.append("prepare"))
    use = Leaf(lambda: calls.append("use"))
    consumer = (prepare >> use).graph(interface=GraphInterface(inputs={"in": use}))
    graph = Graph(
        structure=GraphStructure(
            nodes=(provider, consumer),
            edges=(
                Edge(
                    provider, consumer, ports=("out", "in"), condition=lambda: enabled
                ),
            ),
        )
    )
    with pytest.raises(RuntimeError, match="provider failed"):
        Execution(Graph(structure=graph.structure)).run(mode=mode)
    assert calls == (["prepare"] if enabled else ["prepare", "use"])


def test_static_exclusion_removes_port_connection_and_its_condition():
    calls = []
    output = Leaf(lambda: pytest.fail("excluded provider ran"))
    provider = Graph(
        structure=GraphStructure(
            nodes=(output,), interface=GraphInterface(outputs={"out": output})
        )
    )
    use = Leaf(lambda: calls.append("use"))
    consumer = Graph(
        structure=GraphStructure(
            nodes=(use,), interface=GraphInterface(inputs={"in": use})
        )
    )

    def condition():
        return pytest.fail("excluded condition evaluated")

    graph = Graph(
        structure=GraphStructure(
            nodes=(provider, consumer),
            edges=(Edge(provider, consumer, ports=("out", "in"), condition=condition),),
            static=StaticConditions(
                evaluator=lambda item: bool(item.payload),
                conditions={
                    provider: StaticCondition(payload=False, description="no provider"),
                    (provider, consumer): StaticCondition(
                        payload=False, description="no connection"
                    ),
                },
            ),
        )
    )
    execution = Execution(Graph(structure=graph.structure))
    execution.run()
    assert calls == ["use"]
    document = export_graph(execution.compile())
    assert not any(edge["kind"] == "port" for edge in document["edges"])
    assert document["excluded_edges"] == ["graph:0/edge:0"]


def test_condition_with_all_port_consumers_excluded_is_not_exported_or_evaluated():
    output, unused = Leaf(lambda: None), Leaf(lambda: pytest.fail("excluded input ran"))

    def condition():
        return pytest.fail("unused condition evaluated")

    provider = Graph(
        structure=GraphStructure(
            nodes=(output,), interface=GraphInterface(outputs={"out": output})
        )
    )
    consumer = Graph(
        structure=GraphStructure(
            nodes=(unused,),
            interface=GraphInterface(inputs={"in": unused}),
            static=StaticConditions(
                evaluator=lambda item: bool(item.payload),
                conditions={
                    unused: StaticCondition(payload=False, description="unused input")
                },
            ),
        )
    )
    execution = Execution(
        Graph(
            structure=GraphStructure(
                nodes=(provider, consumer),
                edges=(
                    Edge(provider, consumer, ports=("out", "in"), condition=condition),
                ),
            )
        )
    )
    execution.run()
    document = export_graph(execution.compile())
    assert not any(edge["kind"] == "port" for edge in document["edges"])
    assert document["conditions"] == []


def test_same_port_graph_can_be_mounted_twice_with_distinct_condition_inputs():
    decisions = iter((False, True))
    seen = []
    probe = Leaf(lambda: next(decisions))
    output, use = Leaf(lambda: None), Leaf(lambda: None)
    provider = Graph(
        structure=GraphStructure(
            nodes=(output,), interface=GraphInterface(outputs={"out": output})
        )
    )
    consumer = Graph(
        structure=GraphStructure(
            nodes=(use,), interface=GraphInterface(inputs={"in": use})
        )
    )

    def decide(result):
        seen.append(result.unwrap())
        return result.unwrap()

    condition = decide
    child = Graph(
        structure=GraphStructure(
            nodes=(probe, provider, consumer),
            edges=(
                Edge(
                    provider,
                    consumer,
                    ports=("out", "in"),
                    condition=(condition, probe),
                ),
            ),
        )
    )
    execution = Execution(
        Graph(
            structure=GraphStructure(
                nodes=(
                    Graph(structure=child.structure),
                    Graph(structure=child.structure),
                )
            )
        )
    )
    execution.run()
    assert sorted(seen) == [False, True]
    document = export_graph(execution.compile())
    conditions = document["conditions"]
    assert len(conditions) == 2
    assert conditions[0]["inputs"] != conditions[1]["inputs"]
