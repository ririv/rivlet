"""Validate declaration topology, result bindings and mounted interfaces."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

from .._native import dag_cycle
from ..errors import GraphError
from ..topology import Arc
from .action import LeafAction
from .edge import Edge, ResultEdge, _condition_uses
from .graph import Graph
from .step import Step, StepId, resolve_step_label

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ..interface import GraphInterface
    from . import ResultReceiver
    from .edge import GraphEdge


def _validated_blocking_arcs(
    edges: tuple[GraphEdge, ...],
    *,
    steps: tuple[Step, ...],
    known_steps: set[Step],
    graph_label: str,
) -> Iterator[Arc[Step]]:
    """Validate domain edges while projecting every blocking relationship."""

    # I 为条件输入引用数。普通边与条件输入共同投影阻塞关系，
    # 连同节点校验与环检测，总时间/辅助空间 O(V + E + I)。
    seen: set[Arc[Step]] = set()

    def unique_arc(source: Step, target: Step) -> Arc[Step] | None:
        arc = Arc(source=source, target=target)
        if arc in seen:
            return None
        seen.add(arc)
        return arc

    for edge in edges:
        if isinstance(edge, Edge) and edge.ports is not None:
            if edge.source not in known_steps:
                raise GraphError(
                    f"graph '{graph_label}' has edge from unknown step: {edge.source.label}"
                )
            if edge.target not in known_steps:
                raise GraphError(
                    f"graph '{graph_label}' has edge to unknown step: {edge.target.label}"
                )
            from ..interface import (
                _require_port_output,
            )

            output_name, input_name = edge.ports
            _require_port_output(edge.source, output_name)
            consumer_graph = edge.target.action
            if not isinstance(consumer_graph, Graph):
                raise GraphError(
                    f"port consumer must be a graph-valued step: {edge.target.label}"
                )
            if input_name not in consumer_graph.interface.inputs:
                raise GraphError(
                    f"component {edge.target.label} does not require port {input_name}"
                )
            if edge.source.condition is not None:
                raise GraphError(
                    f"graph '{graph_label}' port provider component must not "
                    f"have a runtime condition: {edge.source.label}"
                )
            provider_graph = edge.source.action
            if isinstance(provider_graph, Graph):
                output = provider_graph.interface.outputs[output_name]
                if output.condition is not None:
                    raise GraphError(
                        f"graph '{graph_label}' port output step must not have "
                        f"a runtime condition: {output_name}"
                    )
            continue

        arc = unique_arc(edge.source, edge.target)
        if arc is not None:
            yield arc

    for target, condition in _condition_uses(steps, edges):
        for source in condition.inputs:
            if source not in known_steps:
                raise GraphError(
                    f"graph '{graph_label}' condition input references a step outside this graph"
                )
            arc = unique_arc(source, target)
            if arc is not None:
                yield arc


def _validate_result_bindings(
    steps: tuple[Step, ...], edges: tuple[GraphEdge, ...]
) -> None:
    # O(V + E)，不为每个消费者重新扫描所有边。
    slots: dict[tuple[Step, ResultReceiver], set[int]] = {}
    for edge in edges:
        if isinstance(edge, ResultEdge):
            if not (
                isinstance(edge.receiver, LeafAction)
                and edge.receiver is edge.target.action
            ):
                raise GraphError(
                    "result receiver does not belong to this step or is not callable"
                )
            indexes = slots.setdefault((edge.target, edge.receiver), set())
            if edge.argument_index < 0 or edge.argument_index in indexes:
                raise GraphError(
                    "result argument indexes must be unique and non-negative"
                )
            indexes.add(edge.argument_index)
    for indexes in slots.values():
        if indexes != set(range(len(indexes))):
            raise GraphError("result argument indexes must be contiguous from zero")
    for step in steps:
        if isinstance(step.action, LeafAction):
            step.action.validate_inputs(len(slots.get((step, step.action), ())))
    for _, condition in _condition_uses(steps, edges):
        count = len(condition.inputs)
        try:
            inspect.signature(condition.when).bind(*([object()] * count))
        except (TypeError, ValueError) as exc:
            raise GraphError(
                f"condition signature does not match {count} result inputs: {exc}"
            ) from exc


def _validate_graph_topology(
    *,
    steps: tuple[Step, ...],
    edges: tuple[GraphEdge, ...],
    graph_label: str,
) -> None:
    known_steps = _validated_steps(steps, graph_label=graph_label)
    step_indexes = {step: index for index, step in enumerate(steps)}
    blocking_arcs = tuple(
        _validated_blocking_arcs(
            edges,
            steps=steps,
            known_steps=known_steps,
            graph_label=graph_label,
        )
    )
    try:
        cycle_indexes = dag_cycle(
            len(steps),
            [
                (
                    step_indexes.get(arc.source, len(steps)),
                    step_indexes.get(arc.target, len(steps)),
                )
                for arc in blocking_arcs
            ],
        )
    except ValueError as exc:
        raise GraphError(str(exc)) from exc
    if cycle_indexes is not None:
        cycle = " -> ".join(
            resolve_step_label(steps[index], StepId((index,)))
            for index in cycle_indexes
        )
        raise GraphError(f"graph '{graph_label}' has dependency cycle: {cycle}")


def _validate_port_interface(
    *,
    steps: tuple[Step, ...],
    interface: GraphInterface,
    graph_label: str,
) -> None:
    # 一次遍历挂载结构建立计数，避免为每个端口重新扫描全部子图。
    # V 为所有挂载的步骤数，R 为端口引用数：时间 O(V + R)，空间 O(V)。
    if not interface.inputs and not interface.outputs:
        return
    mount_counts: dict[Step, int] = {}
    pending = list(steps)
    while pending:
        step = pending.pop()
        mount_counts[step] = mount_counts.get(step, 0) + 1
        if isinstance(step.action, Graph):
            pending.extend(step.action.steps)

    for port, input_steps in interface.inputs.items():
        for step in input_steps:
            mount_count = mount_counts.get(step, 0)
            if mount_count == 0:
                raise GraphError(
                    f"graph '{graph_label}' port input "
                    f"'{port}' references a step outside this graph"
                )
            if mount_count > 1:
                raise GraphError(
                    f"graph '{graph_label}' port input "
                    f"'{port}' references a step mounted more than once"
                )
    for port, step in interface.outputs.items():
        mount_count = mount_counts.get(step, 0)
        if mount_count == 0:
            raise GraphError(
                f"graph '{graph_label}' port output "
                f"'{port}' references a step outside this graph"
            )
        if mount_count > 1:
            raise GraphError(
                f"graph '{graph_label}' port output "
                f"'{port}' references a step mounted more than once"
            )


def _validated_steps(
    steps: tuple[Step, ...],
    *,
    graph_label: str,
) -> set[Step]:
    known_steps: set[Step] = set()
    for index, step in enumerate(steps):
        if not isinstance(step, Step):
            raise GraphError(
                "Graph.steps accepts only Step; use Graph.from_wiring() for StepWiring"
            )
        if step in known_steps:
            raise GraphError(
                f"graph '{graph_label}' contains the same step more than once: "
                f"{resolve_step_label(step, StepId((index,)))}"
            )
        known_steps.add(step)
    return known_steps


def validate_graph(graph: Graph) -> None:
    """Validate one complete declaration without evaluating host callables."""
    if graph.label == "":
        raise GraphError("graph label must not be empty")

    graph_label = graph.display_label
    _validate_graph_topology(
        steps=graph.steps,
        edges=graph.edges,
        graph_label=graph_label,
    )
    _validate_result_bindings(graph.steps, graph.edges)
    _validate_port_interface(
        steps=graph.steps,
        interface=graph.interface,
        graph_label=graph_label,
    )
