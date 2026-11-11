"""Validate orthogonal port routing and execution conditions."""

from __future__ import annotations

from threading import Event

import pytest

from rivlet import (
    AsyncGraphRunOptions,
    Edge,
    Graph,
    GraphError,
    GraphExecution,
    GraphInterface,
    ResultEdge,
    StaticCondition,
    StaticConditions,
    Step,
    ThreadGraphRunOptions,
    export_graph,
    runtime_condition,
)


def require(event: Event) -> None:
    assert event.wait(2), "an independent graph branch was unexpectedly blocked"


def test_same_name_shortcut_and_pair_are_the_same_declaration():
    a, b = Step(lambda: None), Step(lambda: None)
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

    ready = Step(provide, label="ready")
    provider = (
        (ready >> Step(finish, label="tail"))
        .graph(
            interface=GraphInterface(outputs={"sdk-ready": ready}),
        )
        .step(label="provider")
    )
    first, second = Step(lambda: use(used_a)), Step(lambda: use(used_b))
    consumer = (
        (Step(prepared.set) >> (first | second))
        .graph(
            interface=GraphInterface(inputs={"toolchain-ready": (first, second)}),
        )
        .step(label="consumer")
    )

    def decide():
        # 条件的结果输入不能把整个 consumer 挡住，否则 prepare 永远无法运行。
        require(prepared)
        return enabled

    probe = Step(decide, label="probe")

    def predicate(result):
        calls.append(result.unwrap())
        return result.unwrap() is True

    condition = runtime_condition(when=predicate)
    unused = Step(lambda: pytest.fail("static exclusion did not apply"))
    graph = Graph(
        steps=(unused, probe, provider, consumer)
        if project
        else (probe, provider, consumer),
        edges=(
            ResultEdge(probe, consumer, 0, condition),
            Edge(
                provider,
                consumer,
                ports=("sdk-ready", "toolchain-ready"),
                condition=condition,
            ),
        ),
        static=StaticConditions(
            evaluator=lambda item: bool(item.payload),
            conditions={unused: StaticCondition(payload=False, description="disabled")},
        )
        if project
        else (),
    )
    execution = GraphExecution(graph)
    execution.run(
        AsyncGraphRunOptions(concurrency=4)
        if asynchronous
        else ThreadGraphRunOptions(workers=4),
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
    ready, use = Step(lambda: None), Step(lambda: None)
    a = Graph(steps=(ready,), interface=GraphInterface(outputs={"out": ready})).step()
    b = Graph(steps=(use,), interface=GraphInterface(inputs={"in": use})).step()
    Graph(steps=(a, b), edges=(Edge(a, b, ports=("out", "in")),))
    # 任意名称来自用户声明，无法通过 Python 类型预知；必须校验真实图接口。
    with pytest.raises(GraphError, match="does not provide port in"):
        Graph(steps=(a, b), edges=(Edge(a, b, ports=("in", "out")),))


@pytest.mark.parametrize("enabled", (False, True))
@pytest.mark.parametrize("mode", ("compiled", "dynamic"))
def test_port_condition_controls_failure_propagation(enabled, mode):
    calls = []

    def fail():
        raise RuntimeError("provider failed")

    output = Step(fail)
    provider = Graph(
        steps=(output,), interface=GraphInterface(outputs={"out": output})
    ).step()
    prepare = Step(lambda: calls.append("prepare"))
    use = Step(lambda: calls.append("use"))
    consumer = (
        (prepare >> use).graph(interface=GraphInterface(inputs={"in": use})).step()
    )
    graph = Graph(
        steps=(provider, consumer),
        edges=(
            Edge(
                provider,
                consumer,
                ports=("out", "in"),
                condition=runtime_condition(when=lambda: enabled),
            ),
        ),
    )
    with pytest.raises(RuntimeError, match="provider failed"):
        GraphExecution(graph).run(mode=mode)
    assert calls == (["prepare"] if enabled else ["prepare", "use"])


def test_static_exclusion_removes_port_connection_and_its_condition():
    calls = []
    output = Step(lambda: pytest.fail("excluded provider ran"))
    provider = Graph(
        steps=(output,), interface=GraphInterface(outputs={"out": output})
    ).step()
    use = Step(lambda: calls.append("use"))
    consumer = Graph(steps=(use,), interface=GraphInterface(inputs={"in": use})).step()
    condition = runtime_condition(
        when=lambda: pytest.fail("excluded condition evaluated")
    )
    graph = Graph(
        steps=(provider, consumer),
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
    execution = GraphExecution(graph)
    execution.run()
    assert calls == ["use"]
    document = export_graph(execution.compile())
    assert not any(edge["kind"] == "port" for edge in document["edges"])
    assert document["excluded_edges"] == ["graph:root/edge:0"]


def test_condition_with_all_port_consumers_excluded_is_not_exported_or_evaluated():
    output, unused = Step(lambda: None), Step(lambda: pytest.fail("excluded input ran"))
    condition = runtime_condition(
        when=lambda: pytest.fail("unused condition evaluated")
    )
    provider = Graph(
        steps=(output,), interface=GraphInterface(outputs={"out": output})
    ).step()
    consumer = Graph(
        steps=(unused,),
        interface=GraphInterface(inputs={"in": unused}),
        static=StaticConditions(
            evaluator=lambda item: bool(item.payload),
            conditions={
                unused: StaticCondition(payload=False, description="unused input")
            },
        ),
    ).step()
    execution = GraphExecution(
        Graph(
            steps=(provider, consumer),
            edges=(Edge(provider, consumer, ports=("out", "in"), condition=condition),),
        )
    )
    execution.run()
    document = export_graph(execution.compile())
    assert not any(edge["kind"] == "port" for edge in document["edges"])
    assert document["conditions"] == []


def test_same_port_graph_can_be_mounted_twice_with_distinct_condition_inputs():
    decisions = iter((False, True))
    seen = []
    probe = Step(lambda: next(decisions))
    output, use = Step(lambda: None), Step(lambda: None)
    provider = Graph(
        steps=(output,), interface=GraphInterface(outputs={"out": output})
    ).step()
    consumer = Graph(steps=(use,), interface=GraphInterface(inputs={"in": use})).step()

    def decide(result):
        seen.append(result.unwrap())
        return result.unwrap()

    condition = runtime_condition(when=decide)
    child = Graph(
        steps=(probe, provider, consumer),
        edges=(
            ResultEdge(probe, consumer, 0, condition),
            Edge(provider, consumer, ports=("out", "in"), condition=condition),
        ),
    )
    execution = GraphExecution(Graph(steps=(child.step(), child.step())))
    execution.run()
    assert sorted(seen) == [False, True]
    document = export_graph(execution.compile())
    conditions = document["conditions"]
    assert len(conditions) == 2
    assert conditions[0]["inputs"] != conditions[1]["inputs"]
