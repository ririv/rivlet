"""Read graph declarations without compiling or evaluating host code."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, assert_never

from .._native import NativeGraph
from ..capability import CapabilityBinding
from ..errors import GraphError
from ..model import (
    ConditionalEdge,
    Graph,
    LeafAction,
    ResultEdge,
    RuntimeCondition,
    StepId,
    UnconditionalEdge,
    resolve_step_label,
    step_ids_for_mount,
)

if TYPE_CHECKING:
    from ..model import Step
    from .schema import (
        DeclarationDocument,
        EdgeDocument,
        InputPortDocument,
        NodeDocument,
        OutputPortDocument,
        ScopeDocument,
    )


def scope_id(owner: StepId | None) -> str:
    return "graph:root" if owner is None else f"graph:{owner}"


def edge_id(owner: StepId | None, index: int) -> str:
    return f"{scope_id(owner)}/edge:{index}"


@dataclass(slots=True)
class Scope:
    graph: Graph | NativeGraph
    owner: StepId | None
    label_path: tuple[str, ...]
    ids: dict[Step, StepId] = field(default_factory=dict)
    descendants: dict[Step, list[StepId]] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return scope_id(self.owner)

    def require_descendant(self, step: Step) -> str:
        matches = self.descendants[step]
        if len(matches) != 1:
            raise GraphError("exported capability must identify one mounted node")
        return str(matches[0])


@dataclass(slots=True)
class DeclarationIndex:
    document: DeclarationDocument
    scopes: dict[str, Scope] = field(default_factory=dict)
    node_scopes: dict[str, Scope] = field(default_factory=dict)
    condition_objects: dict[tuple[str, RuntimeCondition], str] = field(
        default_factory=dict
    )
    edge_indexes: dict[str, int] = field(default_factory=dict)

    def condition_id(self, target: str, condition: RuntimeCondition) -> str:
        key = (target, condition)
        if key not in self.condition_objects:
            identifier = f"{target}/condition:{len(self.condition_objects)}"
            self.condition_objects[key] = identifier
            self.document["conditions"].append({"id": identifier, "inputs": []})
        return self.condition_objects[key]


def index_declaration(
    graph: Graph,
    *,
    parent: StepId | None = None,
    label: str | None = None,
    label_path: tuple[str, ...] = (),
) -> DeclarationIndex:
    root = scope_id(parent)
    document: DeclarationDocument = {
        "schema_version": 1,
        "view": "declaration",
        "root": root,
        "parent": None if parent is None else str(parent),
        "graphs": [],
        "nodes": [],
        "edges": [],
        "conditions": [],
        "static_rules": [],
        "selections": [],
    }
    index = DeclarationIndex(document)

    # V/E/P 为节点、边和端口引用数，L 为全部身份/标签路径长度。
    # 按挂载遍历并建立索引，时间/空间 O(V + E + P + L)；不按每条边重扫子图。
    def visit(
        current: Graph | NativeGraph,
        owner: StepId | None,
        path: tuple[str, ...],
        ancestors: tuple[Scope, ...],
    ) -> None:
        scope = Scope(current, owner, path)
        index.scopes[scope.id] = scope
        if isinstance(current, Graph):
            scope.ids = step_ids_for_mount(current, parent_step_id=owner)
            scope_label = (
                (current.display_label if label is None else label)
                if not ancestors
                else (current.label if current.label is not None else path[-1])
            )
            scope_document: ScopeDocument = {
                "id": scope.id,
                "label": scope_label,
                "label_path": list(path),
                "owner": None if owner is None else str(owner),
                "nodes": [str(value) for value in scope.ids.values()],
                "ports": [],
            }
            document["graphs"].append(scope_document)
            for step, identifier in scope.ids.items():
                key = str(identifier)
                index.node_scopes[key] = scope
                for ancestor in (*ancestors, scope):
                    ancestor.descendants.setdefault(step, []).append(identifier)
                step_label = resolve_step_label(step, identifier)
                condition = (
                    None
                    if step.condition is None
                    else index.condition_id(key, step.condition)
                )
                action = step.action
                node: NodeDocument
                if isinstance(action, (Graph, NativeGraph)):
                    node = {
                        "kind": "subgraph",
                        "id": key,
                        "graph": scope.id,
                        "label": step_label,
                        "label_path": [*path, step_label],
                        "child_graph": scope_id(identifier),
                        "condition": condition,
                    }
                    document["nodes"].append(node)
                    visit(action, identifier, (*path, step_label), (*ancestors, scope))
                elif isinstance(action, LeafAction):
                    node = {
                        "kind": "step",
                        "id": key,
                        "graph": scope.id,
                        "label": step_label,
                        "label_path": [*path, step_label],
                        "action_kind": "python",
                        "condition": condition,
                    }
                    document["nodes"].append(node)
                else:
                    assert_never(action)
            for capability, steps in current.capabilities.inputs.items():
                port: InputPortDocument = {
                    "kind": "input",
                    "capability": capability.name,
                    "nodes": [scope.require_descendant(step) for step in steps],
                }
                scope_document["ports"].append(port)
            for capability, step in current.capabilities.outputs.items():
                output: OutputPortDocument = {
                    "kind": "output",
                    "capability": capability.name,
                    "node": scope.require_descendant(step),
                }
                scope_document["ports"].append(output)
        else:
            labels, arcs = current.structure()
            ids = [
                owner.child(i) if owner is not None else StepId((i,))
                for i in range(len(labels))
            ]
            document["graphs"].append(
                {
                    "id": scope.id,
                    "label": current.label,
                    "label_path": list(path),
                    "owner": None if owner is None else str(owner),
                    "nodes": [str(value) for value in ids],
                    "ports": [],
                }
            )
            for identifier, native_label in zip(ids, labels, strict=True):
                index.node_scopes[str(identifier)] = scope
                document["nodes"].append(
                    {
                        "kind": "step",
                        "id": str(identifier),
                        "graph": scope.id,
                        "label": native_label,
                        "label_path": [*path, native_label],
                        "action_kind": "native",
                        "condition": None,
                    }
                )
            for i, (source, target) in enumerate(arcs):
                document["edges"].append(
                    {
                        "kind": "order",
                        "id": edge_id(owner, i),
                        "graph": scope.id,
                        "source": str(ids[source]),
                        "target": str(ids[target]),
                    }
                )

    visit(graph, parent, label_path, ())
    for scope in index.scopes.values():
        if isinstance(scope.graph, Graph):
            _export_scope(index, scope)
    conditions = {condition["id"]: condition for condition in document["conditions"]}
    inputs: dict[str, dict[int, str]] = {}
    for edge in document["edges"]:
        if edge["kind"] == "result" and edge["receiver"]["kind"] == "condition":
            for receiver in edge["receiver"]["conditions"]:
                inputs.setdefault(receiver, {})[edge["argument_index"]] = edge["source"]
    for identifier, arguments in inputs.items():
        conditions[identifier]["inputs"] = [arguments[i] for i in range(len(arguments))]
    return index


def _export_scope(index: DeclarationIndex, scope: Scope) -> None:
    graph = scope.graph
    assert isinstance(graph, Graph)
    document = index.document
    for i, edge in enumerate(graph.edges):
        source, target = str(scope.ids[edge.source]), str(scope.ids[edge.target])
        identifier = edge_id(scope.owner, i)
        index.edge_indexes[identifier] = i
        item: EdgeDocument
        if isinstance(edge, UnconditionalEdge):
            item = {
                "kind": "order",
                "id": identifier,
                "graph": scope.id,
                "source": source,
                "target": target,
            }
        elif isinstance(edge, ConditionalEdge):
            item = {
                "kind": "conditional",
                "id": identifier,
                "graph": scope.id,
                "source": source,
                "target": target,
                "condition": index.condition_id(target, edge.condition),
            }
        elif isinstance(edge, ResultEdge):
            assert isinstance(edge.receiver, (LeafAction, RuntimeCondition))
            item = {
                "kind": "result",
                "id": identifier,
                "graph": scope.id,
                "source": source,
                "target": target,
                "argument_index": edge.argument_index,
                "receiver": {"kind": "action"}
                if isinstance(edge.receiver, LeafAction)
                else {
                    "kind": "condition",
                    "conditions": [index.condition_id(target, edge.receiver)],
                },
            }
        elif isinstance(edge, CapabilityBinding):
            provider = index.scopes[scope_id(scope.ids[edge.source])]
            consumer = index.scopes[scope_id(scope.ids[edge.target])]
            provider_graph, consumer_graph = provider.graph, consumer.graph
            assert isinstance(provider_graph, Graph) and isinstance(
                consumer_graph, Graph
            )
            item = {
                "kind": "capability",
                "id": identifier,
                "graph": scope.id,
                "source": source,
                "target": target,
                "capability": edge.capability.name,
                "provider_node": provider.require_descendant(
                    provider_graph.capabilities.outputs[edge.capability]
                ),
                "consumer_nodes": [
                    consumer.require_descendant(step)
                    for step in consumer_graph.capabilities.inputs[edge.capability]
                ],
            }
        else:
            assert_never(edge)
        document["edges"].append(item)
    for i, (target, _binding) in enumerate(graph._static_bindings.items()):
        condition = f"{scope.id}/static:{i}"
        if isinstance(target, tuple):
            document["static_rules"].append(
                {
                    "kind": "edge",
                    "source": str(scope.ids[target[0]]),
                    "target": str(scope.ids[target[1]]),
                    "condition": condition,
                }
            )
        else:
            document["static_rules"].append(
                {"kind": "node", "node": str(scope.ids[target]), "condition": condition}
            )
    for i, selection in enumerate(graph.static_selections):
        document["selections"].append(
            {
                "id": f"{scope.id}/selection:{i}",
                "cases": [
                    {"node": str(scope.ids[case.step]), "condition": case.condition}
                    for case in selection.cases
                ],
            }
        )
