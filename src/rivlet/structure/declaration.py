"""Read graph declarations without compiling or evaluating host code."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .._native import NativeGraphStructure
from ..errors import GraphError
from ..interface import InputEndpoint, InputPort, OutputEndpoint, OutputPort
from ..model import (
    Graph as GraphNode,
)
from ..model import (
    GraphStructure,
    Leaf,
    NodePath,
    _RuntimeCondition,
    node_paths_for_mount,
    resolve_node_label,
)

if TYPE_CHECKING:
    from ..model import Node
    from .schema import (
        DeclarationDocument,
        EdgeDocument,
        InputPortDocument,
        NodeDocument,
        OutputPortDocument,
        ScopeDocument,
    )


def scope_id(owner: NodePath | None) -> str:
    return "graph:root" if owner is None else f"graph:{owner}"


def edge_id(owner: NodePath | None, index: int) -> str:
    return f"{scope_id(owner)}/edge:{index}"


@dataclass(slots=True)
class Scope:
    graph: GraphStructure | NativeGraphStructure
    owner: NodePath | None
    label_path: tuple[str, ...]
    ids: dict[Node, NodePath] = field(default_factory=dict)
    descendants: dict[Node, list[NodePath]] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return scope_id(self.owner)

    def require_descendant(self, step: Node) -> str:
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

    def output_node(self, scope: Scope, endpoint: OutputEndpoint) -> str:
        if isinstance(endpoint, OutputPort):
            path = scope.descendants[endpoint.graph]
            if len(path) != 1:
                raise GraphError("exported port must identify one mounted graph node")
            child = self.scopes[scope_id(path[0])]
            assert isinstance(child.graph, GraphStructure)
            return self.output_node(child, child.graph.interface.outputs[endpoint.name])
        return scope.require_descendant(endpoint)

    def input_nodes(self, scope: Scope, endpoint: InputEndpoint) -> list[str]:
        if isinstance(endpoint, InputPort):
            path = scope.descendants[endpoint.graph]
            if len(path) != 1:
                raise GraphError("exported port must identify one mounted graph node")
            child = self.scopes[scope_id(path[0])]
            assert isinstance(child.graph, GraphStructure)
            return [
                value
                for target in child.graph.interface.inputs[endpoint.name]
                for value in self.input_nodes(child, target)
            ]
        return [scope.require_descendant(endpoint)]

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
    graph: GraphStructure,
    *,
    parent: NodePath | None = None,
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
        current: GraphStructure | NativeGraphStructure,
        owner: NodePath | None,
        path: tuple[str, ...],
        ancestors: tuple[Scope, ...],
    ) -> None:
        scope = Scope(current, owner, path)
        index.scopes[scope.id] = scope
        if isinstance(current, GraphStructure):
            scope.ids = node_paths_for_mount(current, parent_node_path=owner)
            scope_label = (
                ("<structure>" if label is None else label)
                if not ancestors
                else path[-1]
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
                step_label = resolve_node_label(step, identifier)
                condition = (
                    None
                    if step.condition is None
                    else index.condition_id(key, step.condition)
                )
                action = step.structure if isinstance(step, GraphNode) else step
                node: NodeDocument
                if isinstance(action, (GraphStructure, NativeGraphStructure)):
                    node = {
                        "kind": "subgraph",
                        "node_id": step.id.value,
                        "id": key,
                        "graph": scope.id,
                        "label": step_label,
                        "label_path": [*path, step_label],
                        "child_graph": scope_id(identifier),
                        "condition": condition,
                    }
                    document["nodes"].append(node)
                    visit(action, identifier, (*path, step_label), (*ancestors, scope))
                elif isinstance(action, Leaf):
                    node = {
                        "kind": "leaf",
                        "node_id": step.id.value,
                        "id": key,
                        "graph": scope.id,
                        "label": step_label,
                        "label_path": [*path, step_label],
                        "action_kind": "python",
                        "condition": condition,
                    }
                    document["nodes"].append(node)
                else:
                    raise GraphError(f"unsupported node declaration: {type(action)!r}")
            for port_name, steps in current.interface.inputs.items():
                port: InputPortDocument = {
                    "kind": "input",
                    "port": port_name,
                    "nodes": [
                        value
                        for step in steps
                        for value in index.input_nodes(scope, step)
                    ],
                }
                scope_document["ports"].append(port)
            for port_name, step in current.interface.outputs.items():
                output: OutputPortDocument = {
                    "kind": "output",
                    "port": port_name,
                    "node": index.output_node(scope, step),
                }
                scope_document["ports"].append(output)
        else:
            labels, arcs = current.structure()
            ids = [
                owner.child(i) if owner is not None else NodePath((i,))
                for i in range(len(labels))
            ]
            document["graphs"].append(
                {
                    "id": scope.id,
                    "label": path[-1],
                    "label_path": list(path),
                    "owner": None if owner is None else str(owner),
                    "nodes": [str(value) for value in ids],
                    "ports": [],
                }
            )
            for identifier, native_label, identity in zip(
                ids, labels, current.node_ids(), strict=True
            ):
                index.node_scopes[str(identifier)] = scope
                document["nodes"].append(
                    {
                        "kind": "leaf",
                        "id": str(identifier),
                        "node_id": identity,
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
        if isinstance(scope.graph, GraphStructure):
            _export_scope(index, scope)
    return index


def _export_scope(index: DeclarationIndex, scope: Scope) -> None:
    graph = scope.graph
    assert isinstance(graph, GraphStructure)
    document = index.document
    for i, edge in enumerate(graph.edges):
        source, target = str(scope.ids[edge.source]), str(scope.ids[edge.target])
        identifier = edge_id(scope.owner, i)
        index.edge_indexes[identifier] = i
        item: EdgeDocument
        if edge._result_index is not None:
            item = {
                "kind": "result",
                "id": identifier,
                "graph": scope.id,
                "source": source,
                "target": target,
                "argument_index": edge._result_index,
            }
            if isinstance(edge.result_source, OutputPort):
                item["source_port"] = edge.result_source.name
                item["provider_node"] = index.output_node(scope, edge.result_source)
            if isinstance(edge.result_target, InputPort):
                item["target_port"] = edge.result_target.name
                item["consumer_nodes"] = index.input_nodes(scope, edge.result_target)
        elif edge.ports is not None:
            provider = index.scopes[scope_id(scope.ids[edge.source])]
            consumer = index.scopes[scope_id(scope.ids[edge.target])]
            provider_graph, consumer_graph = provider.graph, consumer.graph
            assert isinstance(provider_graph, GraphStructure) and isinstance(
                consumer_graph, GraphStructure
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
                "provider_node": index.output_node(
                    provider, provider_graph.interface.outputs[edge.ports[0]]
                ),
                "consumer_nodes": [
                    value
                    for step in consumer_graph.interface.inputs[edge.ports[1]]
                    for value in index.input_nodes(consumer, step)
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
                    {"node": str(scope.ids[case.node]), "condition": case.condition}
                    for case in selection.cases
                ],
            }
        )
