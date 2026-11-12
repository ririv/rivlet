from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..compile import (
    CompiledComposite,
    CompiledCompositeRef,
    CompiledConditionalDependency,
    CompiledLeaf,
    CompiledPrerequisite,
    CompiledRuntimeCondition,
    CompiledStepRef,
    GraphCompilation,
    compile_graph,
)
from ..errors import GraphError
from ..model import (
    ConditionalPrerequisite,
    Edge,
    Graph,
    GraphEdge,
    ResultEdge,
    Step,
    StepPath,
    StepPrerequisite,
    _condition_uses,
    _RuntimeCondition,
    resolve_step_label,
)
from ..static_condition import StaticCondition

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..results import GraphResultView


def ensure_compilation(
    subject: Graph | GraphCompilation,
    *,
    root_label: str,
) -> GraphCompilation:
    if isinstance(subject, GraphCompilation):
        return subject
    return compile_graph(subject, root_label=root_label)


def render_step_label(
    step: Step | CompiledLeaf,
    *,
    step_path: StepPath | None = None,
) -> str:
    if isinstance(step, Step):
        if step_path is None:
            raise ValueError("declaration step label requires its mounted step id")
        return resolve_step_label(step, step_path)
    return step.path.labels[-1]


def render_compiled_step_label(step: CompiledLeaf, path: tuple[str, ...]) -> str:
    del step
    return render_step_path(path)


def render_condition_suffix(
    step: Step | CompiledLeaf | CompiledComposite,
) -> str:
    condition = step.condition
    if condition is None:
        return ""
    return f" [condition: {condition_name(condition)}]"


def render_after_suffix(
    step: CompiledLeaf | CompiledComposite,
    *,
    step_labels: Mapping[StepPath, str] | None = None,
) -> str:
    if not step.after:
        return ""
    deps = ", ".join(
        format_prerequisite(dep, step_labels=step_labels) for dep in step.after
    )
    return f" [after: {deps}]"


def render_graph_after_suffix(
    graph: Graph,
    step: Step,
    *,
    step_paths: Mapping[Step, StepPath],
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
            f"{resolve_step_label(source, step_paths[source])} ({receiver} input: {index})"
            for index, source in enumerate(condition.inputs)
        )
    return "" if not deps else f" [after: {', '.join(deps)}]"


def render_static_suffix(graph: Graph, step: Step) -> str:
    binding = graph._static_bindings.get(step)
    condition = (
        ""
        if binding is None
        else f" [static: {format_static_condition(binding.condition)}]"
    )
    return condition + render_selection_suffix(graph, step)


def render_selection_suffix(graph: Graph, step: Step) -> str:
    binding = graph._selection_bindings.get(step)
    if binding is None:
        return ""
    _, case = binding
    return f" [select: {case.condition}]"


def render_graph_port_suffix(graph: Graph, step: Step) -> str:
    bindings = tuple(
        edge
        for edge in graph.edges
        if isinstance(edge, Edge) and edge.ports is not None and edge.target is step
    )
    if not bindings:
        return ""
    requirements = ", ".join(
        f"{edge.ports[1]} <- {edge.ports[0]} @ "
        f"{'<unlabeled>' if edge.source.label is None else edge.source.label}"
        + (
            ""
            if edge.condition is None
            else f" (条件边: {condition_name(edge.condition)})"
        )
        for edge in bindings
        if edge.ports is not None
    )
    return f" [requires: {requirements}]"


def format_graph_edge(
    edge: GraphEdge,
    *,
    graph: Graph,
    step_paths: Mapping[Step, StepPath],
) -> str:
    dep_id = resolve_step_label(edge.source, step_paths[edge.source])
    binding = graph._static_bindings.get((edge.source, edge.target))
    if binding is not None:
        dep_id += f" (static: {format_static_condition(binding.condition)})"
    if isinstance(edge, Edge) and edge.condition is not None:
        return f"{dep_id} (条件边: {condition_name(edge.condition)})"
    if isinstance(edge, ResultEdge):
        return f"{dep_id} ({result_receiver_label(edge)} input: {edge.argument_index})"
    return dep_id


def result_receiver_label(edge: ResultEdge) -> str:
    if edge.receiver is edge.target.action:
        return "action"
    raise GraphError("result receiver must be the target action")


def render_step_path(path: tuple[str, ...]) -> str:
    return ".".join(path)


def format_prerequisite(
    dep: StepPrerequisite | CompiledPrerequisite,
    *,
    step_labels: Mapping[StepPath, str] | None = None,
) -> str:
    def _label(step_path: StepPath) -> str:
        if step_labels is None:
            return str(step_path)
        return step_labels[step_path]

    match dep:
        case ConditionalPrerequisite(step=source, condition=condition):
            if source.label is None:
                raise ValueError(
                    "declaration prerequisite label requires its mounted step id"
                )
            return f"{source.label} (条件边: {condition_name(condition)})"
        case CompiledConditionalDependency(node=node, condition=condition):
            dep_id = _label(node.path)
            return f"{dep_id} (条件边: {condition_name(condition)})"
        case CompiledStepRef(path=dep_id) | CompiledCompositeRef(path=dep_id):
            return _label(dep_id)
        case Step() as source:
            if source.label is None:
                raise ValueError("declaration step label requires its mounted step id")
            return source.label
        case StepPath() as step_path:
            return _label(step_path)


def prerequisite_step_label(dep: StepPrerequisite) -> str | None:
    if isinstance(dep, ConditionalPrerequisite):
        return dep.step.label
    return dep.label


def condition_name(
    condition: StaticCondition | _RuntimeCondition | CompiledRuntimeCondition,
) -> str:
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


def escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def mermaid_graph_node_id(graph_path: tuple[str, ...]) -> str:
    return "graph_" + "_".join(sanitize_mermaid_token(part) for part in graph_path)


def mermaid_step_node_id(
    graph_path: tuple[str, ...],
    step_label: str,
    index: int,
) -> str:
    path_token = "_".join(sanitize_mermaid_token(part) for part in graph_path)
    return f"step_{path_token}_{index}_{sanitize_mermaid_token(step_label)}"


def sanitize_mermaid_token(value: str) -> str:
    sanitized = "".join(char if char.isalnum() else "_" for char in value)
    sanitized = sanitized.strip("_")
    if not sanitized:
        return "node"
    if sanitized[0].isdigit():
        return f"n_{sanitized}"
    return sanitized


def render_execution_result_suffix(
    step_path: StepPath,
    results: GraphResultView | None,
) -> str:
    if results is None:
        return ""
    result = results.get(step_path)
    if result is None:
        return ""
    value = result.value
    if not isinstance(value, str | int | float | bool | type(None)):
        return ""
    return f" [result={json.dumps(value, ensure_ascii=False)}]"
