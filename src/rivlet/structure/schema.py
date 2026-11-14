"""Versioned JSON-only inspection documents, never executable graph inputs."""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict


class OrderEdgeDocument(TypedDict):
    kind: Literal["order"]
    id: str
    graph: str
    source: str
    target: str


class ConditionalEdgeDocument(TypedDict):
    kind: Literal["conditional"]
    id: str
    graph: str
    source: str
    target: str
    condition: str


class ResultEdgeDocument(TypedDict):
    kind: Literal["result"]
    id: str
    graph: str
    source: str
    target: str
    argument_index: int
    source_port: NotRequired[str | None]
    target_port: NotRequired[str | None]
    provider_node: NotRequired[str]
    consumer_nodes: NotRequired[list[str]]


class PortEdgeDocument(TypedDict):
    kind: Literal["port"]
    id: str
    graph: str
    source: str
    target: str
    source_port: str | None
    target_port: str | None
    condition: str | None
    provider_node: str
    consumer_nodes: list[str]


type EdgeDocument = (
    OrderEdgeDocument | ConditionalEdgeDocument | ResultEdgeDocument | PortEdgeDocument
)


class StepDocument(TypedDict):
    # schema v2 的 wire tag 不随 Python/Rust 声明类改名；此分支对应 Step。
    kind: Literal["leaf"]
    id: str
    node_id: int
    graph: str
    label: str
    label_path: list[str]
    action_kind: Literal["python", "native"]
    condition: str | None


class SubgraphDocument(TypedDict):
    kind: Literal["subgraph"]
    id: str
    node_id: int
    graph: str
    label: str
    label_path: list[str]
    child_graph: str
    condition: str | None


type NodeDocument = StepDocument | SubgraphDocument


class InputPortDocument(TypedDict):
    kind: Literal["input"]
    port: str | None
    nodes: list[str]


class OutputPortDocument(TypedDict):
    kind: Literal["output"]
    port: str | None
    node: str


class ScopeDocument(TypedDict):
    id: str
    label: str
    label_path: list[str]
    owner: str | None
    nodes: list[str]
    ports: list[InputPortDocument | OutputPortDocument]


class ConditionDocument(TypedDict):
    id: str
    inputs: list[str]


class CompiledConditionDocument(TypedDict):
    id: str
    index: int
    inputs: list[str]


class StaticNodeRuleDocument(TypedDict):
    kind: Literal["node"]
    node: str
    condition: str


class StaticEdgeRuleDocument(TypedDict):
    kind: Literal["edge"]
    source: str
    target: str
    condition: str


class SelectionCaseDocument(TypedDict):
    node: str
    condition: bool


class SelectionDocument(TypedDict):
    id: str
    cases: list[SelectionCaseDocument]


class RequiredDependencyDocument(TypedDict):
    kind: Literal["required"]
    source: str
    target: str


class ConditionalDependencyDocument(TypedDict):
    kind: Literal["conditional"]
    source: str
    target: str
    condition: str


class DeclarationDocument(TypedDict):
    schema_version: Literal[2]
    view: Literal["declaration"]
    root: str
    parent: str | None
    graphs: list[ScopeDocument]
    nodes: list[NodeDocument]
    edges: list[EdgeDocument]
    conditions: list[ConditionDocument]
    static_rules: list[StaticNodeRuleDocument | StaticEdgeRuleDocument]
    selections: list[SelectionDocument]


class CompilationDocument(TypedDict):
    schema_version: Literal[2]
    view: Literal["compiled"]
    root: str
    parent: str | None
    graphs: list[ScopeDocument]
    nodes: list[NodeDocument]
    edges: list[EdgeDocument]
    conditions: list[CompiledConditionDocument]
    dependencies: list[RequiredDependencyDocument | ConditionalDependencyDocument]
    excluded_nodes: list[str]
    excluded_edges: list[str]


type GraphDocument = DeclarationDocument | CompilationDocument
