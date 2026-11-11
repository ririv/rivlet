from __future__ import annotations

from threading import Event

import pytest

from rivlet import (
    AnyProviderRequirement,
    ExactProviderRequirement,
    Graph,
    GraphError,
    GraphExecution,
    GraphInterface,
    GraphMode,
    PortConnection,
    ProviderSelection,
    Step,
    ThreadGraphRunOptions,
    resolve_port_requirements,
)


def _input_of(component: Step) -> str:
    action = component.action
    assert isinstance(action, Graph)
    return next(iter(action.interface.inputs))


@pytest.mark.parametrize("name", ("", " ready", "ready "))
def test_port_names_are_validated_at_declaration_boundaries(name: str) -> None:
    step = Step(lambda: None)
    for declare in (
        lambda: GraphInterface(inputs={name: step}),
        lambda: GraphInterface(outputs={name: step}),
        lambda: AnyProviderRequirement(consumer=step, port=name),
        lambda: ExactProviderRequirement(consumer=step, provider=step, port=name),
    ):
        with pytest.raises(
            GraphError, match="port name must be non-empty and stripped"
        ):
            declare()


def test_port_interface_copies_mappings_and_reuses_normalized_inputs() -> None:
    sdk = "sdk"
    library = "library"
    configure = Step(lambda: None)
    install = Step(lambda: None)
    inputs = {sdk: configure}
    outputs = {library: install}
    shared = GraphInterface(inputs=inputs, outputs=outputs)
    inputs.clear()
    outputs.clear()
    assert dict(shared.inputs) == {sdk: (configure,)}
    assert dict(shared.outputs) == {library: install}
    reused = GraphInterface(inputs=shared.inputs)
    assert reused.inputs == shared.inputs
    assert not reused.outputs


def test_graph_forwards_complete_or_selected_interface_without_flattening() -> None:
    sdk = "sdk"
    library = "library"
    final = "final"
    configure = Step(lambda: None)
    install = Step(lambda: None)
    finish = Step(lambda: None)
    child = (configure >> install).graph(
        interface=GraphInterface(inputs={sdk: configure}, outputs={library: install})
    )
    forwarded = (child >> finish).graph(interface=child)
    assert forwarded.interface is child.interface
    assert forwarded.steps[0].action is child
    custom = (child >> finish).graph(
        interface=GraphInterface(inputs=child, outputs={final: finish})
    )
    assert custom.interface.inputs == child.interface.inputs
    assert dict(custom.interface.outputs) == {final: finish}
    outputs_only = Graph(steps=(child.step(),), interface=GraphInterface(outputs=child))
    assert not outputs_only.interface.inputs
    assert outputs_only.interface.outputs == child.interface.outputs
    # 不声明接口时，不自动暴露内部图的端口。
    assert not (child >> finish).graph().interface.inputs
    assert not (child >> finish).graph().interface.outputs


@pytest.mark.parametrize("mode", ("compiled", "dynamic"))
def test_one_input_unlocks_multiple_independent_steps(mode: GraphMode) -> None:
    sdk = "sdk"
    a_prepared, b_prepared, a_started, b_started, sdk_ready = (
        Event() for _ in range(5)
    )

    def provide() -> None:
        _require_event(a_prepared, "a preparation did not run before SDK")
        _require_event(b_prepared, "b preparation did not run before SDK")
        sdk_ready.set()

    def configure_a() -> None:
        assert sdk_ready.is_set()
        a_started.set()
        _require_event(b_started, "b input was serialized after a")

    def configure_b() -> None:
        assert sdk_ready.is_set()
        b_started.set()
        _require_event(a_started, "a input was serialized after b")

    ready = Step(provide)
    provider = Graph(
        steps=(ready,), interface=GraphInterface(outputs={sdk: ready})
    ).step()
    a, b = Step(configure_a), Step(configure_b)
    consumer = (
        ((Step(a_prepared.set) >> a) | (Step(b_prepared.set) >> b))
        .graph(interface=GraphInterface(inputs={sdk: (a, b)}))
        .step()
    )
    bindings = resolve_port_requirements(
        (provider, consumer),
        (ExactProviderRequirement(consumer=consumer, provider=provider, port=sdk),),
    )
    GraphExecution(
        graph=Graph(steps=(provider, consumer), edges=bindings, label="root")
    ).run(ThreadGraphRunOptions(workers=4), mode=mode)
    assert a_started.is_set() and b_started.is_set()


def test_interface_rejects_empty_input_and_unmounted_or_repeated_steps() -> None:
    sdk = "sdk"
    step = Step(lambda: None)
    with pytest.raises(GraphError, match="at least one"):
        GraphInterface(inputs={sdk: ()})
    with pytest.raises(GraphError, match="duplicate steps"):
        GraphInterface(inputs={sdk: (step, step)})
    with pytest.raises(GraphError, match="outside this graph"):
        Graph(interface=GraphInterface(inputs={sdk: step}))
    child = Graph(steps=(step,))
    with pytest.raises(GraphError, match="mounted more than once"):
        Graph(
            steps=(child.step(), child.step()),
            interface=GraphInterface(inputs={sdk: step}),
        )


def _require_event(event: Event, message: str) -> None:
    if not event.wait(timeout=2.0):
        raise AssertionError(message)


def _component(
    label: str,
    *,
    port_input: tuple[str, Step] | None = None,
    port_output: tuple[str, Step] | None = None,
    steps: tuple[Step, ...],
) -> Step:
    graph = Graph.from_wiring(
        tuple(
            step.wire(after=() if index == 0 else (steps[index - 1],))
            for index, step in enumerate(steps)
        ),
        label=label,
        interface=GraphInterface(
            inputs=dict(
                () if port_input is None else ((port_input[0], port_input[1]),)
            ),
            outputs=dict(
                () if port_output is None else ((port_output[0], port_output[1]),)
            ),
        ),
    )
    return graph.step(label=label)


def test_port_connection_unlocks_only_the_consumer_input() -> None:
    port = "123"
    consumer_prepared = Event()
    consumer_started = Event()

    provider_steps = (
        Step(lambda: None, label="1"),
        Step(lambda: None, label="2"),
        Step(
            lambda: _require_event(consumer_prepared, "consumer did not prepare"),
            label="3",
        ),
        Step(
            lambda: _require_event(consumer_started, "consumer did not start"),
            label="4",
        ),
        Step(lambda: None, label="5"),
    )
    consumer_steps = (
        Step(consumer_prepared.set, label="a"),
        Step(consumer_started.set, label="b"),
        Step(lambda: None, label="c"),
        Step(lambda: None, label="d"),
        Step(lambda: None, label="e"),
    )
    provider = _component(
        "A",
        port_output=(port, provider_steps[2]),
        steps=provider_steps,
    )
    consumer = _component(
        "B",
        port_input=(port, consumer_steps[1]),
        steps=consumer_steps,
    )
    bindings = resolve_port_requirements(
        (provider, consumer),
        (AnyProviderRequirement(consumer=consumer, port=_input_of(consumer)),),
    )
    graph = Graph(
        label="port",
        steps=(provider, consumer),
        edges=bindings,
    )

    GraphExecution(graph=graph).run(
        ThreadGraphRunOptions(workers=2),
        mode="compiled",
    )

    assert consumer_prepared.is_set()
    assert consumer_started.is_set()


@pytest.mark.parametrize("mode", ("compiled", "dynamic"))
def test_direct_connections_gate_each_named_port_independently(mode: GraphMode) -> None:
    library_ready = "library-ready"
    docs_ready = "docs-ready"
    installed, library_used, docs_generated, docs_used = (Event() for _ in range(4))

    def generate_docs() -> None:
        # 库消费者必须能先启动，不能因为同一接口还公开了文档出口而被阻塞。
        _require_event(library_used, "library consumer waited for the entire interface")
        docs_generated.set()

    def use_library() -> None:
        assert installed.is_set()
        assert not docs_generated.is_set()
        library_used.set()

    def use_docs() -> None:
        assert library_used.is_set()
        assert docs_generated.is_set()
        docs_used.set()

    install = Step(installed.set, label="install-library")
    docs = Step(generate_docs, label="generate-docs")
    provider = (
        (install >> docs)
        .graph(
            interface=GraphInterface(
                outputs={library_ready: install, docs_ready: docs}
            ),
        )
        .step(label="provider")
    )

    compile_step = Step(use_library, label="compile-with-library")
    package_step = Step(use_docs, label="package-with-docs")
    consumer = (
        (compile_step >> package_step)
        .graph(
            interface=GraphInterface(
                inputs={library_ready: compile_step, docs_ready: package_step},
            ),
        )
        .step(label="consumer")
    )

    graph = Graph(
        steps=(provider, consumer),
        edges=(
            PortConnection(source=provider, target=consumer, port=library_ready),
            PortConnection(source=provider, target=consumer, port=docs_ready),
        ),
    )
    GraphExecution(graph).run(ThreadGraphRunOptions(workers=2), mode=mode)
    assert docs_used.is_set()


def test_exact_provider_requirement_selects_the_named_provider() -> None:
    port = "123"
    a_ready = Step(lambda: None, label="ready")
    c_ready = Step(lambda: None, label="ready")
    b_needs = Step(lambda: None, label="needs")
    provider_a = _component(
        "A",
        port_output=(port, a_ready),
        steps=(a_ready,),
    )
    provider_c = _component(
        "C",
        port_output=(port, c_ready),
        steps=(c_ready,),
    )
    consumer = _component(
        "B",
        port_input=(port, b_needs),
        steps=(b_needs,),
    )

    assert (
        resolve_port_requirements(
            (provider_a, provider_c, consumer),
            (
                ExactProviderRequirement(
                    consumer=consumer,
                    provider=provider_a,
                    port=_input_of(consumer),
                ),
            ),
        )[0].source
        is provider_a
    )


def test_exact_requirements_allow_multiple_providers_for_one_input() -> None:
    port = "123"
    a_ready = Step(lambda: None, label="ready")
    c_ready = Step(lambda: None, label="ready")
    b_needs = Step(lambda: None, label="needs")
    provider_a = _component(
        "A",
        port_output=(port, a_ready),
        steps=(a_ready,),
    )
    provider_c = _component(
        "C",
        port_output=(port, c_ready),
        steps=(c_ready,),
    )
    consumer = _component(
        "B",
        port_input=(port, b_needs),
        steps=(b_needs,),
    )
    input_port = _input_of(consumer)

    bindings = resolve_port_requirements(
        (provider_a, provider_c, consumer),
        (
            ExactProviderRequirement(consumer, provider_a, input_port),
            ExactProviderRequirement(consumer, provider_c, input_port),
        ),
    )

    assert tuple(binding.source for binding in bindings) == (
        provider_a,
        provider_c,
    )


def test_port_port_can_explicitly_export_descendant_step() -> None:
    port = "123"
    ready = Step(lambda: None, label="ready")
    needs = Step(lambda: None, label="needs")
    provider_graph = Graph.from_wiring(
        wiring=(Graph.from_wiring(wiring=(ready.wire(),)).step().wire(),),
        interface=GraphInterface(outputs={port: ready}),
    )
    consumer_graph = Graph.from_wiring(
        wiring=(Graph.from_wiring(wiring=(needs.wire(),)).step().wire(),),
        interface=GraphInterface(inputs={port: needs}),
    )
    provider = provider_graph.step(label="A")
    consumer = consumer_graph.step(label="B")
    bindings = resolve_port_requirements(
        (provider, consumer),
        (
            ExactProviderRequirement(
                consumer,
                provider,
                port,
            ),
        ),
    )

    GraphExecution(
        graph=Graph(steps=(provider, consumer), edges=bindings, label="root"),
    ).run(mode="compiled")


def test_any_provider_requirement_uses_explicit_selection_when_ambiguous() -> None:
    port = "123"
    a_ready = Step(lambda: None)
    c_ready = Step(lambda: None)
    b_needs = Step(lambda: None)
    provider_a = _component(
        "A",
        port_output=(port, a_ready),
        steps=(a_ready,),
    )
    provider_c = _component(
        "C",
        port_output=(port, c_ready),
        steps=(c_ready,),
    )
    consumer = _component(
        "B",
        port_input=(port, b_needs),
        steps=(b_needs,),
    )
    requirement = AnyProviderRequirement(
        consumer=consumer,
        port=_input_of(consumer),
    )

    with pytest.raises(GraphError, match="provider is ambiguous"):
        resolve_port_requirements(
            (provider_a, provider_c, consumer),
            (requirement,),
        )

    assert (
        resolve_port_requirements(
            (provider_a, provider_c, consumer),
            (requirement,),
            selections=(
                ProviderSelection(
                    requirement=requirement,
                    provider=provider_c,
                ),
            ),
        )[0].source
        is provider_c
    )


def test_port_connection_uses_unified_execution_path() -> None:
    port = "123"
    ready = Step(lambda: None)
    needs = Step(lambda: None)
    provider = _component(
        "A",
        port_output=(port, ready),
        steps=(ready,),
    )
    consumer = _component(
        "B",
        port_input=(port, needs),
        steps=(needs,),
    )
    graph = Graph(
        steps=(provider, consumer),
        edges=resolve_port_requirements(
            (provider, consumer),
            (AnyProviderRequirement(consumer=consumer, port=_input_of(consumer)),),
        ),
    )

    GraphExecution(graph=graph).run()
