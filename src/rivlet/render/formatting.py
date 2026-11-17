"""Local annotations and Python live-execution formatting."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .._native import _NativeStep
from ..compile import (
    CompiledPythonStep,
    CompiledRuntimeCondition,
    CompiledRustStep,
    CompiledStep,
    GraphCompilation,
    _compile_graph_scope,
)
from ..interface import InputPort, OutputPort
from ..model import (
    Edge,
    Graph,
    GraphEdge,
    Node,
    NodePath,
    _BoundNativeCondition,
    _condition_uses,
    _RuntimeCondition,
    resolve_node_label,
)
from ..static_condition import StaticCondition

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..results import ResultView


def ensure_compilation(
    subject: Graph | GraphCompilation,
    *,
    root_label: str,
) -> GraphCompilation:
    if isinstance(subject, GraphCompilation):
        return subject
    return _compile_graph_scope(subject, root_label=root_label)


def render_step_label(
    step: Node | CompiledStep,
    *,
    node_path: NodePath | None = None,
) -> str:
    if isinstance(step, CompiledRustStep):
        return f"{step.path.labels[-1]} [native]"
    if isinstance(step, CompiledPythonStep):
        return step.path.labels[-1]
    if node_path is None:
        raise ValueError("declaration node label requires its mounted path")
    label = resolve_node_label(step, node_path)
    from ..model import Step

    return (
        f"{label} [native]"
        if isinstance(step, Step) and isinstance(step.action, _NativeStep)
        else label
    )


def render_graph_after_suffix(
    graph: Graph,
    step: Node,
    *,
    step_paths: Mapping[Node, NodePath],
) -> str:
    deps = (
        [
            format_graph_edge(edge, graph=graph, step_paths=step_paths)
            for edge in incoming
        ]
        if (incoming := graph.incoming_edges(step))
        else []
    )
    for target, condition in _condition_uses((step,), graph.edges):
        if target is not step:
            continue
        receiver = (
            "condition"
            if condition is step.condition
            else f"edge condition {condition_name(condition)}"
        )
        deps.extend(
            f"{resolve_node_label(source.graph if isinstance(source, OutputPort) else source, step_paths[source.graph if isinstance(source, OutputPort) else source])} ({receiver} input: {index})"
            for index, source in enumerate(condition.inputs)
        )
    return "" if not deps else f" [after: {', '.join(deps)}]"


def render_static_suffix(graph: Graph, step: Node) -> str:
    binding = graph._static_bindings.get(step)
    condition = (
        ""
        if binding is None
        else f" [static: {format_static_condition(binding.condition)}]"
    )
    return condition + render_selection_suffix(graph, step)


def render_selection_suffix(graph: Graph, step: Node) -> str:
    binding = graph._selection_bindings.get(step)
    if binding is None:
        return ""
    _, case = binding
    return f" [select: {case.condition}]"


def port_label(name: str | None) -> str:
    """Render anonymous identity without inventing a user-defined name."""
    return "(anonymous)" if name is None else name


def format_graph_edge(
    edge: GraphEdge,
    *,
    graph: Graph,
    step_paths: Mapping[Node, NodePath],
) -> str:
    dep_id = resolve_node_label(edge.source, step_paths[edge.source])
    binding = graph._static_bindings.get((edge.source, edge.target))
    if binding is not None:
        dep_id += f" (static: {format_static_condition(binding.condition)})"
    if isinstance(edge, Edge) and edge.condition is not None:
        return f"{dep_id} (条件边: {condition_name(edge.condition)})"
    if edge._result_index is not None:
        if isinstance(edge.result_source, OutputPort):
            dep_id += f".{port_label(edge.result_source.name)}"
        return f"{dep_id} ({result_edge_label(edge)})"
    return dep_id


def result_edge_label(edge: Edge) -> str:
    """Describe positional result transport without discarding named ports."""

    label = f"action input: {edge._result_index}"
    if isinstance(edge.result_source, OutputPort):
        label += f" from {port_label(edge.result_source.name)}"
    if isinstance(edge.result_target, InputPort):
        label += f" to {port_label(edge.result_target.name)}"
    return label


def condition_name(
    condition: StaticCondition
    | _RuntimeCondition
    | _BoundNativeCondition
    | CompiledRuntimeCondition,
) -> str:
    if isinstance(condition, _BoundNativeCondition):
        return "runtime:rust"
    if isinstance(
        condition,
        (_RuntimeCondition, CompiledRuntimeCondition),
    ):
        qualname = getattr(condition.when, "__qualname__", None)
        if isinstance(qualname, str) and qualname:
            return f"runtime:{qualname}"
        name = getattr(condition.when, "__name__", None)
        if isinstance(name, str) and name:
            return f"runtime:{name}"
        return f"runtime:{condition!r}"
    if isinstance(condition, StaticCondition):
        return f"static:{format_static_condition(condition)}"
    return repr(condition)


def format_static_condition(condition: StaticCondition) -> str:
    return condition.description


def render_execution_result_suffix(
    node_path: NodePath,
    results: ResultView | None,
) -> str:
    if results is None:
        return ""
    result = results.get(node_path)
    if result is None:
        return ""
    value = result.value
    if not isinstance(value, str | int | float | bool | type(None)):
        return ""
    return f" [result={json.dumps(value, ensure_ascii=False)}]"
