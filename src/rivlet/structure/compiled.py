"""Export an existing compilation without running its compiler again."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .._compilation_origins import NodeConditionOrigin
from ..compile import CompiledConditionalDependency
from ..errors import GraphError
from ..model import ConditionalEdge, Graph, RuntimeCondition
from .declaration import edge_id, index_declaration

if TYPE_CHECKING:
    from ..compile import GraphCompilation
    from .schema import (
        CompilationDocument,
        CompiledConditionDocument,
        ConditionalDependencyDocument,
        EdgeDocument,
        InputPortDocument,
        OutputPortDocument,
        RequiredDependencyDocument,
        ScopeDocument,
    )


def export_compilation(compilation: GraphCompilation) -> CompilationDocument:
    origins = compilation._structure_origins
    if origins is None:
        raise GraphError(
            "structural export requires a compilation produced by compile_graph"
        )
    index = index_declaration(
        compilation.source_graph,
        parent=origins.parent_step_id,
        label=compilation.label,
        label_path=compilation.root_label_path,
    )
    declaration = index.document
    nodes = compilation._native_nodes
    active = {str(node.id): node for node in nodes}
    condition_ids = {
        id(condition): f"condition:{i}"
        for i, condition in enumerate(compilation._native_conditions)
    }
    conditions: list[CompiledConditionDocument] = [
        {
            "id": f"condition:{i}",
            "index": i,
            "inputs": [str(ref.id) for ref in condition.inputs],
        }
        for i, condition in enumerate(compilation._native_conditions)
    ]
    condition_instances: dict[tuple[str, RuntimeCondition], list[str]] = {}
    edge_conditions: dict[tuple[str, int], str] = {}
    steps = {
        str(identifier): step
        for scope in index.scopes.values()
        for step, identifier in scope.ids.items()
    }
    for i, origin in enumerate(origins.conditions):
        target = str(origin.target)
        if isinstance(origin, NodeConditionOrigin):
            source_condition = steps[target].condition
        else:
            scope = index.node_scopes[target]
            assert isinstance(scope.graph, Graph)
            edge = scope.graph.edges[origin.edge_index]
            assert isinstance(edge, ConditionalEdge)
            source_condition = edge.condition
            edge_conditions[(target, origin.edge_index)] = f"condition:{i}"
        assert isinstance(source_condition, RuntimeCondition)
        condition_instances.setdefault((target, source_condition), []).append(
            f"condition:{i}"
        )
    receivers = {
        identifier: condition_instances.get(key, [])
        for key, identifier in index.condition_objects.items()
    }
    disabled = {
        edge_id(origins.parent_step_id if owner is None else owner, edge)
        for owner, edge in origins.disabled_edges
    }
    exported_edges: list[EdgeDocument] = []
    for edge in declaration["edges"]:
        source, target = edge["source"], edge["target"]
        if source not in active or target not in active or edge["id"] in disabled:
            continue
        if edge["kind"] == "conditional":
            edge["condition"] = edge_conditions[
                (target, index.edge_indexes[edge["id"]])
            ]
        elif edge["kind"] == "result" and edge["receiver"]["kind"] == "condition":
            targets = [
                condition
                for receiver in edge["receiver"]["conditions"]
                for condition in receivers[receiver]
            ]
            if not targets:
                continue
            edge["receiver"] = {"kind": "condition", "conditions": targets}
        elif edge["kind"] == "port":
            edge["consumer_nodes"] = [
                consumer for consumer in edge["consumer_nodes"] if consumer in active
            ]
            if not edge["consumer_nodes"]:
                continue
            if edge["provider_node"] not in active:
                raise GraphError("compiled port has no active provider")
        exported_edges.append(edge)
    exported_nodes = []
    for node in declaration["nodes"]:
        compiled = active.get(node["id"])
        if compiled is None:
            continue
        node["condition"] = (
            None
            if compiled.condition is None
            else condition_ids[id(compiled.condition)]
        )
        node["label_path"] = list(compiled.context.label_path)
        exported_nodes.append(node)
    graphs: list[ScopeDocument] = []
    for scope in declaration["graphs"]:
        if scope["id"] != declaration["root"] and scope["owner"] not in active:
            continue
        scope["nodes"] = [node for node in scope["nodes"] if node in active]
        ports: list[InputPortDocument | OutputPortDocument] = []
        for port in scope["ports"]:
            if port["kind"] == "input":
                port["nodes"] = [node for node in port["nodes"] if node in active]
                if port["nodes"]:
                    ports.append(port)
            elif port["node"] in active:
                ports.append(port)
        scope["ports"] = ports
        graphs.append(scope)
    dependencies: list[RequiredDependencyDocument | ConditionalDependencyDocument] = []
    for node in nodes:
        for prerequisite in node.after:
            if isinstance(prerequisite, CompiledConditionalDependency):
                dependencies.append(
                    {
                        "kind": "conditional",
                        "source": str(prerequisite.node.id),
                        "target": str(node.id),
                        "condition": condition_ids[id(prerequisite.condition)],
                    }
                )
            else:
                dependencies.append(
                    {
                        "kind": "required",
                        "source": str(prerequisite.id),
                        "target": str(node.id),
                    }
                )
    retained = {edge["id"] for edge in exported_edges}
    # 不从 after 猜原始边类型，也不重新求值静态条件；来源和实际依赖均来自本次编译。
    # V/E/I/P/L 为节点、边、条件实例及其输入引用总数、端口及路径总长度，
    # 时间/空间 O(V + E + I + P + L)，包含同一 predicate 多位置实例化的实际输出量。
    return {
        "schema_version": 2,
        "view": "compiled",
        "root": declaration["root"],
        "parent": declaration["parent"],
        "graphs": graphs,
        "nodes": exported_nodes,
        "edges": exported_edges,
        "conditions": conditions,
        "dependencies": dependencies,
        "excluded_nodes": [
            node["id"] for node in declaration["nodes"] if node["id"] not in active
        ],
        "excluded_edges": [
            edge["id"] for edge in declaration["edges"] if edge["id"] not in retained
        ],
    }
