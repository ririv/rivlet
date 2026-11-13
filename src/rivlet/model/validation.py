"""Validate local structure and declared interfaces before hierarchy compilation."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

from .._native import dag_cycle
from ..errors import GraphError
from ..interface import InputPort, OutputPort, _declared_interface
from .edge import _condition_uses, _ResultConnection
from .graph import Graph
from .graph_structure import GraphStructure
from .identity import NodePath
from .leaf import Leaf
from .node import Node, resolve_node_label

if TYPE_CHECKING:
    from ..interface import GraphInterface, InputEndpoint
    from .edge import _ResultGroup


def _input_nodes(endpoint: InputEndpoint, *, result: bool = False) -> tuple[Node, ...]:
    if isinstance(endpoint, InputPort):
        if not isinstance(endpoint.graph.structure, GraphStructure):
            if result:
                raise GraphError("Rust actions do not accept Python result arguments")
            return ()
        return tuple(
            node
            for target in _declared_interface(endpoint.graph).inputs[endpoint.name]
            for node in _input_nodes(target, result=result)
        )
    return (endpoint,)


def _validate_result_bindings(graph: GraphStructure) -> None:
    # O(V + E + I)：每组结果只校验一次；公开入口允许待绑定，最终编译仍须闭合。
    groups: dict[Node | InputPort, _ResultGroup] = {}
    slots: dict[Node | InputPort, set[int]] = {}
    for edge in graph.edges:
        connection = edge._connection
        if not isinstance(connection, _ResultConnection):
            continue
        target = edge.result_target
        group = groups.setdefault(target, connection.group)
        if group is not connection.group:
            raise GraphError("target action must have one result connection group")
        indexes = slots.setdefault(target, set())
        if connection.index in indexes:
            raise GraphError("result connection must not be repeated")
        indexes.add(connection.index)
    for target, indexes in slots.items():
        if len(indexes) != groups[target].size:
            raise GraphError("result connection group must be included in full")
        for node in _input_nodes(target, result=True):
            if not isinstance(node, Leaf):
                raise GraphError("result target must have a callable action")
            node.validate_inputs(len(indexes))
    exposed = {
        node
        for targets in graph.interface.inputs.values()
        for target in targets
        for node in _input_nodes(target)
    }
    for node in graph.nodes:
        if isinstance(node, Leaf) and node not in exposed and node not in slots:
            node.validate_inputs(0)
    for _, condition in _condition_uses(graph.nodes, graph.edges):
        count = len(condition.inputs)
        try:
            inspect.signature(condition.when).bind(*([object()] * count))
        except (TypeError, ValueError) as exc:
            raise GraphError(
                f"condition signature does not match {count} result inputs: {exc}"
            ) from exc


def _validated_nodes(nodes: tuple[Node, ...], *, graph_label: str) -> set[Node]:
    known: set[Node] = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, (Leaf, Graph)):
            raise GraphError("GraphStructure.nodes accepts only Leaf or Graph nodes")
        if node in known:
            raise GraphError(
                f"graph '{graph_label}' contains the same node more than once: "
                f"{resolve_node_label(node, NodePath((index,)))}"
            )
        known.add(node)
    return known


def _validate_graph_topology(graph: GraphStructure) -> None:
    known = _validated_nodes(graph.nodes, graph_label="<structure>")
    indexes: dict[Node, int] = {node: index for index, node in enumerate(graph.nodes)}
    arcs: set[tuple[int, int]] = set()
    for edge in graph.edges:
        if edge.source not in known or edge.target not in known:
            raise GraphError("graph edge references a node outside this graph")
        if edge.ports is not None:
            from ..interface import _require_declared_port_input, _require_port_output

            output_name, input_name = edge.ports
            _require_port_output(edge.source, output_name)
            _require_declared_port_input(edge.target, input_name)
            if edge.source.condition is not None:
                raise GraphError(
                    "port provider component must not have a runtime condition"
                )
            assert isinstance(edge.source, Graph)
            if isinstance(edge.source.structure, GraphStructure):
                output = _declared_interface(edge.source).outputs[output_name]
                if not isinstance(output, OutputPort) and output.condition is not None:
                    raise GraphError(
                        "port output node must not have a runtime condition"
                    )
            continue
        if isinstance(edge.result_source, OutputPort) or isinstance(
            edge.result_target, InputPort
        ):
            # 跨端口关系的真实端点不一定是子图整体边界，交由 Rust 展开后校验，
            # 不以组件级边近似，避免误报交错消费产生的环。
            continue
        arcs.add((indexes[edge.source], indexes[edge.target]))
    for target, condition in _condition_uses(graph.nodes, graph.edges):
        for source in condition.inputs:
            if source not in known:
                raise GraphError("condition input references a node outside this graph")
            arcs.add((indexes[source], indexes[target]))
    try:
        cycle = dag_cycle(len(graph.nodes), list(arcs))
    except ValueError as exc:
        raise GraphError(str(exc)) from exc
    if cycle is not None:
        labels = " -> ".join(
            resolve_node_label(graph.nodes[index], NodePath((index,)))
            for index in cycle
        )
        raise GraphError(f"graph '<structure>' has dependency cycle: {labels}")


def _validate_port_interface(
    *, nodes: tuple[Node, ...], interface: GraphInterface
) -> None:
    if not interface.inputs and not interface.outputs:
        return
    # O(V + R) 建立挂载计数，端口引用不逐项重扫整张子图。
    counts: dict[Node, int] = {}
    pending = list(nodes)
    while pending:
        node = pending.pop()
        counts[node] = counts.get(node, 0) + 1
        if isinstance(node, Graph) and isinstance(node.structure, GraphStructure):
            pending.extend(node.structure.nodes)
    for port, targets in interface.inputs.items():
        for target in targets:
            node = target.graph if isinstance(target, InputPort) else target
            _require_unique_port_node(counts, node, port, "input")
    for port, target in interface.outputs.items():
        node = target.graph if isinstance(target, OutputPort) else target
        _require_unique_port_node(counts, node, port, "output")


def _require_unique_port_node(
    counts: dict[Node, int], node: Node, port: str, direction: str
) -> None:
    count = counts.get(node, 0)
    if not count:
        raise GraphError(
            f"port {direction} '{port}' references a node outside this graph"
        )
    if count > 1:
        raise GraphError(
            f"port {direction} '{port}' references a node mounted more than once"
        )


def validate_graph(graph: GraphStructure) -> None:
    """Validate local structure, retaining explicitly exposed unresolved inputs."""

    _validate_graph_topology(graph)
    _validate_result_bindings(graph)
    _validate_port_interface(nodes=graph.nodes, interface=graph.interface)
