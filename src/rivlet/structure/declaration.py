"""Read graph declarations without compiling or evaluating host code."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, assert_never

from .._native import NativeGraph
from ..errors import GraphError
from ..model import (
    Graph,
    LeafAction,
    ResultEdge,
    StepId,
    _RuntimeCondition,
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
            raise GraphError("exported port must identify one mounted node")
        return str(matches[0])


@dataclass(slots=True)
class DeclarationIndex:
    document: DeclarationDocument
    scopes: dict[str, Scope] = field(default_factory=dict)
    node_scopes: dict[str, Scope] = field(default_factory=dict)
    condition_objects: dict[tuple[str, _RuntimeCondition], str] = field(
        default_factory=dict
    )
    edge_indexes: dict[str, int] = field(default_factory=dict)

    def condition_id(self, target: str, condition: _RuntimeCondition) -> str:
        key = (target, condition)
        if key not in self.condition_objects:
            identifier = f"{target}/condition:{len(self.condition_objects)}"
            self.condition_objects[key] = identifier
            scope = self.node_scopes[target]
            self.document["conditions"].append(
                {
                    "id": identifier,
                    "inputs": [str(scope.ids[source]) for source in condition.inputs],
                }
            )
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
        "schema_version": 2,
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
            for port_name, steps in current.interface.inputs.items():
                port: InputPortDocument = {
                    "kind": "input",
                    "port": port_name,
                    "nodes": [scope.require_descendant(step) for step in steps],
                }
                scope_document["ports"].append(port)
            for port_name, step in current.interface.outputs.items():
                output: OutputPortDocument = {
                    "kind": "output",
                    "port": port_name,
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
        if isinstance(edge, ResultEdge):
            assert isinstance(edge.receiver, LeafAction)
            item = {
                "kind": "result",
                "id": identifier,
                "graph": scope.id,
                "source": source,
                "target": target,
                "argument_index": edge.argument_index,
                "receiver": {"kind": "action"},
            }
        elif edge.ports is not None:
            provider = index.scopes[scope_id(scope.ids[edge.source])]
            consumer = index.scopes[scope_id(scope.ids[edge.target])]
            provider_graph, consumer_graph = provider.graph, consumer.graph
            assert isinstance(provider_graph, Graph) and isinstance(
                consumer_graph, Graph
            )
            item = {
                "kind": "port",
                "id": identifier,
                "graph": scope.id,
                "source": source,
                "target": target,
                "source_port": edge.ports[0],
                "target_port": edge.ports[1],
                "condition": None
                if edge.condition is None
                else index.condition_id(target, edge.condition),
                "provider_node": provider.require_descendant(
                    provider_graph.interface.outputs[edge.ports[0]]
                ),
                "consumer_nodes": [
                    consumer.require_descendant(step)
                    for step in consumer_graph.interface.inputs[edge.ports[1]]
                ],
            }
        elif edge.condition is not None:
            item = {
                "kind": "conditional",
                "id": identifier,
                "graph": scope.id,
                "source": source,
                "target": target,
                "condition": index.condition_id(target, edge.condition),
            }
        else:
            item = {
                "kind": "order",
                "id": identifier,
                "graph": scope.id,
                "source": source,
                "target": target,
            }
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
