from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

from ._compilation_origins import (
    CompilationOrigins,
    EdgeConditionOrigin,
    NodeConditionOrigin,
)
from ._native import (
    CompiledExecutionGraph,
    DeclarationCompilation,
    RustActionBindings,
    RustGraphStructure,
    compile_declaration,
)
from .errors import GraphError
from .model import GraphObservationMessages, GraphStructure, NodeId, NodePath
from .results import GraphScope

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .model import (
        ConditionalEdgePredicate,
        Node,
        NodeLabelPath,
        Step,
    )


@dataclass(frozen=True, slots=True)
class CompiledStepRef:
    """引用一个会实际执行 action 的编译叶子。"""

    path: NodePath


@dataclass(frozen=True, slots=True)
class CompiledCompositeRef:
    """引用一个只承担组合生命周期的编译边界。"""

    path: NodePath


type CompiledNodeRef = CompiledStepRef | CompiledCompositeRef


def compiled_node_path(ref: CompiledNodeRef) -> NodePath:
    """取得编译节点引用指向的全局唯一 id。"""

    return ref.path


@dataclass(frozen=True, slots=True)
class CompiledRuntimeCondition:
    """运行期 predicate 及其已经映射到编译节点的结果依赖。

    `inputs` 中的节点全部终态后，runtime 才能调用 `when`。predicate
    按声明顺序接收该挂载对应的 Option 结果，编译过程不会提前求值。
    """

    when: ConditionalEdgePredicate
    inputs: tuple[CompiledNodeRef, ...]


@dataclass(frozen=True, slots=True)
class CompiledConditionalDependency:
    """只有条件成立时才要求来源节点完成的编译依赖。"""

    node: CompiledNodeRef
    condition: CompiledRuntimeCondition


type CompiledPrerequisite = CompiledNodeRef | CompiledConditionalDependency


def compiled_prerequisite_node(
    prerequisite: CompiledPrerequisite,
) -> CompiledNodeRef:
    """取得 prerequisite 最终约束的节点，忽略条件是否启用该约束。"""

    if isinstance(prerequisite, CompiledConditionalDependency):
        return prerequisite.node
    return prerequisite


@dataclass(frozen=True, slots=True)
class CompiledNodeContext:
    """编译节点映射回声明图作用域所需的上下文。

    编译会把嵌套节点编码成全局唯一 id，但运行期条件、结果引用和日志
    仍使用声明时的路径与图作用域。该对象保存这种映射，供 step 和
    composite 共用，不携带调度状态。
    """

    node_id: NodeId
    graph_node_paths: Mapping[Node, NodePath]
    ancestor_graph_scopes: tuple[GraphScope, ...]
    ancestor_composite_ids: tuple[NodePath, ...]


@dataclass(frozen=True, slots=True)
class CompiledPythonStep:
    """由 Python 执行器调用的 Python 叶子节点。"""

    ref: CompiledStepRef
    action: Step
    after: tuple[CompiledPrerequisite, ...]
    condition: CompiledRuntimeCondition | None
    owner: CompiledCompositeRef | None
    context: CompiledNodeContext
    inputs: tuple[CompiledNodeRef, ...] = ()

    @property
    def path(self) -> NodePath:
        return self.ref.path

    @property
    def id(self) -> NodeId:
        return self.context.node_id


@dataclass(frozen=True, slots=True)
class CompiledRustStep:
    """由 Rust 执行器直接调用的原生叶子节点。"""

    ref: CompiledStepRef
    after: tuple[CompiledPrerequisite, ...]
    condition: CompiledRuntimeCondition | None
    owner: CompiledCompositeRef | None
    context: CompiledNodeContext
    inputs: tuple[CompiledNodeRef, ...] = ()

    @property
    def path(self) -> NodePath:
        return self.ref.path

    @property
    def id(self) -> NodeId:
        return self.context.node_id


@dataclass(frozen=True, slots=True)
class CompiledComposite:
    """原始子图在编译产物中的非执行生命周期投影。

    子图 action 已经展开为 `CompiledPythonStep`；该类型只保留直接子节点、依赖、
    条件和 observation。runtime 会追踪它的进入与完成，但不会把它提交
    给 worker，也不会为它生成伪造的 no-op step。
    """

    ref: CompiledCompositeRef
    source_graph: GraphStructure | RustGraphStructure
    graph_label: str
    children: tuple[CompiledNodeRef, ...]
    after: tuple[CompiledPrerequisite, ...]
    condition: CompiledRuntimeCondition | None
    owner: CompiledCompositeRef | None
    observation: GraphObservationMessages
    context: CompiledNodeContext
    graph_node_paths: Mapping[Node, NodePath]

    @property
    def path(self) -> NodePath:
        return self.ref.path

    @property
    def id(self) -> NodeId:
        return self.context.node_id


type CompiledStep = CompiledPythonStep | CompiledRustStep
type CompiledNode = CompiledStep | CompiledComposite


@dataclass(frozen=True, slots=True)
class _MaterializeExecutionGraph:
    """Request native execution indexes from the Python compilation model."""


@dataclass(frozen=True, slots=True)
class _PrecompiledExecutionGraph:
    """Carry one complete execution graph compiled by the Rust declaration compiler."""

    graph: CompiledExecutionGraph
    conditions: tuple[CompiledRuntimeCondition, ...]
    nodes: tuple[CompiledNode, ...]
    rust_actions: RustActionBindings


type _ExecutionGraphInput = _MaterializeExecutionGraph | _PrecompiledExecutionGraph

_MATERIALIZE_EXECUTION_GRAPH = _MaterializeExecutionGraph()


@dataclass(frozen=True, slots=True)
class GraphCompilation:
    """声明图的扁平执行计划和组合生命周期记录。

    `steps` 是唯一的 action 执行事实来源。`composites` 不保存可执行子图，
    只描述原始子图边界；`roots` 则明确给出根图直接拥有的节点。
    """

    source_graph: GraphStructure
    label: str
    steps: tuple[CompiledStep, ...]
    composites: tuple[CompiledComposite, ...]
    roots: tuple[CompiledNodeRef, ...]
    root_label_path: NodeLabelPath = ()
    root_graph_node_paths: Mapping[Node, NodePath] = field(
        default_factory=lambda: MappingProxyType({})
    )
    _structure_origins: CompilationOrigins | None = field(
        default=None, repr=False, compare=False
    )
    _execution_graph_input: _ExecutionGraphInput = field(
        default=_MATERIALIZE_EXECUTION_GRAPH,
        repr=False,
        compare=False,
    )
    _execution_graph: CompiledExecutionGraph = field(
        init=False,
        repr=False,
        compare=False,
    )
    _execution_nodes: tuple[CompiledNode, ...] = field(
        init=False,
        repr=False,
        compare=False,
    )
    _execution_node_indexes: Mapping[NodePath, int] = field(
        init=False,
        repr=False,
        compare=False,
    )
    _execution_conditions: tuple[CompiledRuntimeCondition, ...] = field(
        init=False,
        repr=False,
        compare=False,
    )
    _rust_actions: RustActionBindings = field(
        init=False,
        repr=False,
        compare=False,
    )

    @property
    def observation(self) -> GraphObservationMessages:
        return self.source_graph.observation

    def __post_init__(self) -> None:
        if not self.label:
            raise GraphError("compiled graph label must not be empty")

        match self._execution_graph_input:
            case _MaterializeExecutionGraph():
                self._initialize_materialized_execution_graph()
            case _PrecompiledExecutionGraph() as precompiled:
                self._initialize_precompiled_execution_graph(precompiled)

    def _initialize_materialized_execution_graph(self) -> None:
        """Validate public Python inputs and materialize their native execution graph."""

        # 编译产物会被 runtime 直接消费，构造时必须一次性验证引用图，
        # 避免把结构错误延迟到部分 step 已经执行之后。
        # 单次建立节点、owner 和声明作用域索引。设 V 为节点数、S 为所有不同
        # 声明作用域中的 step 总数：时间 O(V + S)，额外空间 O(V + S)。后续
        # composite 校验必须查询这些索引，不能为每个 composite 重扫全部节点。
        nodes: dict[NodePath, CompiledNode] = {}
        child_ids_by_owner: dict[CompiledCompositeRef, set[NodePath]] = {}
        ownerless_node_ids: set[NodePath] = set()
        scope_step_paths: dict[int, frozenset[NodePath]] = {}
        for node in (*self.steps, *self.composites):
            if node.path in nodes:
                raise GraphError(f"compiled graph has duplicate node id: {node.path}")
            nodes[node.path] = node
            if node.owner is None:
                ownerless_node_ids.add(node.path)
            else:
                child_ids_by_owner.setdefault(node.owner, set()).add(node.path)
            scope = node.context.graph_node_paths
            scope_key = id(scope)
            if scope_key not in scope_step_paths:
                scope_step_paths[scope_key] = frozenset(scope.values())

        root_ids = tuple(root.path for root in self.roots)
        if len(root_ids) != len(set(root_ids)):
            raise GraphError("compiled graph has duplicate root references")
        for root in self.roots:
            node = _require_compiled_node(nodes, root)
            if node.owner is not None:
                raise GraphError(f"compiled root has an owner: {node.path}")
        if set(root_ids) != ownerless_node_ids:
            raise GraphError("compiled graph roots do not match ownerless nodes")

        # 节点、直接依赖和 children 只遍历一次：时间 O(V + A)，其中 A 为
        # prerequisite 与 composite-child 引用总数；不再含 O(C * V) 扫描。
        for step in self.steps:
            _validate_compiled_node(
                nodes,
                step,
                scope_step_paths=scope_step_paths[id(step.context.graph_node_paths)],
            )
        for composite in self.composites:
            _validate_compiled_node(
                nodes,
                composite,
                scope_step_paths=scope_step_paths[
                    id(composite.context.graph_node_paths)
                ],
            )
            child_ids = tuple(child.path for child in composite.children)
            if len(child_ids) != len(set(child_ids)):
                raise GraphError(
                    f"compiled composite has duplicate children: {composite.path}"
                )
            for child in composite.children:
                child_node = _require_compiled_node(nodes, child)
                if child_node.owner != composite.ref:
                    raise GraphError(
                        "compiled composite child has a different owner: "
                        f"{child_node.path}"
                    )
            expected_child_ids = child_ids_by_owner.get(composite.ref, set())
            if set(child_ids) != expected_child_ids:
                raise GraphError(
                    "compiled composite children do not match owned nodes: "
                    f"{composite.path}"
                )

        execution_nodes = tuple(nodes.values())
        execution_node_indexes = {
            node.path: index for index, node in enumerate(execution_nodes)
        }
        try:
            execution_graph, execution_conditions, effective_after = (
                _materialize_execution_graph(
                    execution_nodes,
                    execution_node_indexes,
                )
            )
        except ValueError as exc:
            raise GraphError(str(exc)) from exc

        if any(
            node.after != lowered_after
            for node, lowered_after in zip(
                execution_nodes, effective_after, strict=True
            )
        ):
            execution_nodes = tuple(
                replace(node, after=lowered_after)
                for node, lowered_after in zip(
                    execution_nodes,
                    effective_after,
                    strict=True,
                )
            )
            object.__setattr__(
                self,
                "steps",
                tuple(
                    node
                    for node in execution_nodes
                    if not isinstance(node, CompiledComposite)
                ),
            )
            object.__setattr__(
                self,
                "composites",
                tuple(
                    node
                    for node in execution_nodes
                    if isinstance(node, CompiledComposite)
                ),
            )

        execution_root_ids = {
            execution_nodes[index].path for index in execution_graph.roots()
        }
        if execution_root_ids != ownerless_node_ids:
            raise GraphError("compiled graph native roots do not match ownerless nodes")
        for composite in self.composites:
            composite_index = execution_node_indexes[composite.path]
            execution_child_ids = {
                execution_nodes[index].path
                for index in execution_graph.children(composite_index)
            }
            if execution_child_ids != {child.path for child in composite.children}:
                raise GraphError(
                    "compiled graph native children do not match composite: "
                    f"{composite.path}"
                )

        self._set_execution_graph(
            graph=execution_graph,
            nodes=execution_nodes,
            node_indexes=execution_node_indexes,
            conditions=execution_conditions,
            rust_actions=RustActionBindings(len(execution_nodes)),
        )

    def _initialize_precompiled_execution_graph(
        self,
        precompiled: _PrecompiledExecutionGraph,
    ) -> None:
        """Bind opaque Python side tables to a Rust-validated execution graph."""

        # Rust 已经完成声明结构、拓扑、owner、children 与 port 校验。
        # Python 这里只验证跨语言侧表仍引用同一批对象，时间与空间均为 O(V)；
        # 不能再把整个编译图重复验证一遍。
        declared_nodes = (*self.steps, *self.composites)
        if len(precompiled.nodes) != len(declared_nodes) or {
            id(node) for node in precompiled.nodes
        } != {id(node) for node in declared_nodes}:
            raise GraphError("precompiled execution nodes do not match compiled nodes")

        execution_node_indexes = {
            node.path: index for index, node in enumerate(precompiled.nodes)
        }
        if len(execution_node_indexes) != len(precompiled.nodes):
            raise GraphError("precompiled execution graph has duplicate node ids")

        self._set_execution_graph(
            graph=precompiled.graph,
            nodes=precompiled.nodes,
            node_indexes=execution_node_indexes,
            conditions=precompiled.conditions,
            rust_actions=precompiled.rust_actions,
        )

    def _set_execution_graph(
        self,
        *,
        graph: CompiledExecutionGraph,
        nodes: tuple[CompiledNode, ...],
        node_indexes: dict[NodePath, int],
        conditions: tuple[CompiledRuntimeCondition, ...],
        rust_actions: RustActionBindings,
    ) -> None:
        """Commit the complete native execution view as one internal state transition."""

        object.__setattr__(self, "_execution_graph", graph)
        object.__setattr__(self, "_execution_nodes", nodes)
        object.__setattr__(self, "_execution_conditions", conditions)
        object.__setattr__(self, "_rust_actions", rust_actions)
        object.__setattr__(
            self,
            "_execution_node_indexes",
            MappingProxyType(node_indexes),
        )

    def _dependent_refs(self, node_id: NodePath) -> tuple[CompiledNodeRef, ...]:
        node_index = self._execution_node_indexes[node_id]
        return tuple(
            self._execution_nodes[index].ref
            for index in self._execution_graph.dependents(node_index)
        )

    def _execution_node(self, index: int) -> CompiledNode:
        return self._execution_nodes[index]

    def _execution_condition(self, index: int) -> CompiledRuntimeCondition:
        return self._execution_conditions[index]


def _require_compiled_node(
    nodes: dict[NodePath, CompiledNode],
    ref: CompiledNodeRef,
) -> CompiledNode:
    """解析 typed ref，并验证引用类别与实际节点类别一致。

    `NodePath` 在扁平图中全局唯一，但只有 id 存在还不够：
    `CompiledStepRef` 不能指向 `CompiledComposite`，反之亦然。
    """

    node = nodes.get(ref.path)
    if node is None:
        raise GraphError(f"compiled graph references unknown node: {ref.path}")
    if isinstance(ref, CompiledStepRef) != (not isinstance(node, CompiledComposite)):
        raise GraphError(
            f"compiled graph reference kind does not match node: {ref.path}"
        )
    return node


def _validate_compiled_node(
    nodes: dict[NodePath, CompiledNode],
    node: CompiledNode,
    *,
    scope_step_paths: frozenset[NodePath],
) -> None:
    """验证单个节点的声明作用域、所有权和直接依赖引用。"""

    if node.path not in scope_step_paths:
        raise GraphError(
            f"compiled node source scope does not contain its id: {node.path}"
        )
    if node.owner is not None:
        owner = _require_compiled_node(nodes, node.owner)
        if not isinstance(owner, CompiledComposite):
            raise GraphError(f"compiled node owner is not a composite: {node.path}")
    for prerequisite in node.after:
        _require_compiled_node(
            nodes,
            compiled_prerequisite_node(prerequisite),
        )
        if isinstance(prerequisite, CompiledConditionalDependency):
            _validate_compiled_condition(nodes, prerequisite.condition)
    if node.condition is not None:
        _validate_compiled_condition(nodes, node.condition)
    if isinstance(node, CompiledPythonStep):
        node.action.validate_inputs(len(node.inputs))
        for ref in node.inputs:
            _require_compiled_node(nodes, ref)


def _validate_compiled_condition(
    nodes: dict[NodePath, CompiledNode],
    condition: CompiledRuntimeCondition,
) -> None:
    """验证运行期条件读取的每个结果来源仍存在于编译产物中。"""

    for read in condition.inputs:
        _require_compiled_node(nodes, read)


def _materialize_execution_graph(
    nodes: tuple[CompiledNode, ...],
    node_indexes: Mapping[NodePath, int],
) -> tuple[
    CompiledExecutionGraph,
    tuple[CompiledRuntimeCondition, ...],
    tuple[tuple[CompiledPrerequisite, ...], ...],
]:
    """一次性把 Python 编译结构交给 Rust 建立执行索引。"""

    condition_reads: list[list[int]] = []
    conditions: list[CompiledRuntimeCondition] = []

    def register_condition(condition: CompiledRuntimeCondition) -> int:
        condition_index = len(condition_reads)
        condition_reads.append([node_indexes[read.path] for read in condition.inputs])
        conditions.append(condition)
        return condition_index

    composite_indexes = tuple(
        index for index, node in enumerate(nodes) if isinstance(node, CompiledComposite)
    )
    composite_child_graphs = {
        index: graph_index
        for graph_index, index in enumerate(composite_indexes, start=1)
    }
    graph_owners: list[int | None] = [None, *composite_indexes]
    node_graphs: list[int] = []
    prerequisites: list[list[tuple[int, int | None]]] = []
    node_conditions: list[int | None] = []
    for node in nodes:
        node_graphs.append(
            0
            if node.owner is None
            else composite_child_graphs[node_indexes[node.owner.path]]
        )
        prerequisites.append(
            [
                (
                    node_indexes[compiled_prerequisite_node(prerequisite).path],
                    register_condition(prerequisite.condition)
                    if isinstance(prerequisite, CompiledConditionalDependency)
                    else None,
                )
                for prerequisite in node.after
            ]
        )
        if isinstance(node, CompiledPythonStep):
            prerequisites[-1].extend(
                (node_indexes[ref.path], None) for ref in node.inputs
            )
        node_conditions.append(
            None if node.condition is None else register_condition(node.condition)
        )

    execution_graph = CompiledExecutionGraph(
        node_graphs,
        list(composite_child_graphs.items()),
        graph_owners,
        prerequisites,
        node_conditions,
        condition_reads,
        [],
    )
    effective_after = tuple(
        tuple(
            nodes[node_index].ref
            if condition_index is None
            else CompiledConditionalDependency(
                node=nodes[node_index].ref,
                condition=conditions[condition_index],
            )
            for node_index, condition_index in prerequisites
        )
        for prerequisites in execution_graph.all_prerequisites()
    )
    return (
        execution_graph,
        tuple(conditions),
        effective_after,
    )


def compile_graph(
    source_graph: GraphStructure,
    *,
    root_label: str,
    parent_node_path: NodePath | None = None,
    root_label_path: NodeLabelPath = (),
    ancestor_graph_scopes: tuple[GraphScope, ...] = (),
) -> GraphCompilation:
    """把完整 Python 声明一次性交给 Rust 编译，并恢复不透明宿主对象侧表。"""

    if parent_node_path is not None and not root_label_path:
        root_label_path = parent_node_path.labels
    try:
        native = compile_declaration(
            source_graph,
            root_label,
            list(root_label_path),
            [] if parent_node_path is None else list(parent_node_path.segments),
        )
    except ValueError as exc:
        raise GraphError(str(exc)) from exc
    return _bind_declaration_compilation(
        source_graph,
        native,
        external_ancestor_graph_scopes=ancestor_graph_scopes,
    )


def _bind_declaration_compilation(
    source_graph: GraphStructure,
    native: DeclarationCompilation,
    *,
    external_ancestor_graph_scopes: tuple[GraphScope, ...] = (),
) -> GraphCompilation:
    """只恢复 action、predicate 与声明作用域，不参与任何图结构决策。"""

    active_declarations = native.declaration_nodes()
    declaration_to_compiled = {
        declaration: index for index, declaration in enumerate(active_declarations)
    }
    step_ids = tuple(NodeId(value) for value in native.node_ids())
    labels = native.node_labels()
    node_graphs = native.node_graphs()
    child_graphs = native.node_child_graphs()
    source_steps = tuple(
        None if value is None else cast("Node", value)
        for value in native.source_steps()
    )
    actions = native.actions()
    graph_labels = native.graph_labels()
    graph_label_paths = tuple(tuple(value) for value in native.graph_label_paths())
    step_paths = tuple(
        NodePath(tuple(value), labels=(*graph_label_paths[graph_index], label))
        for value, graph_index, label in zip(
            native.node_paths(), node_graphs, labels, strict=True
        )
    )
    graph_owners = native.graph_owners()
    graph_local_nodes = native.graph_local_nodes()
    source_graphs = tuple(native.source_graphs())

    refs_by_declaration: dict[int, CompiledNodeRef] = {}
    for compiled_index, declaration in enumerate(active_declarations):
        node_path = step_paths[compiled_index]
        refs_by_declaration[declaration] = (
            CompiledCompositeRef(node_path)
            if child_graphs[compiled_index] is not None
            else CompiledStepRef(node_path)
        )

    graph_node_paths = tuple(
        MappingProxyType(
            {
                cast("Node", source_steps[compiled_index]): step_paths[compiled_index]
                for declaration in local_nodes
                if declaration in declaration_to_compiled
                for compiled_index in (declaration_to_compiled[declaration],)
                if source_steps[compiled_index] is not None
            }
        )
        for local_nodes in graph_local_nodes
    )

    # 同一图的节点共享不可变祖先上下文。深度 D：从 O(V_active * D)
    # 收敛为 O(G_active * D + V_active)，祖先对象也只按图创建。
    scopes_by_graph: dict[int, tuple[GraphScope, ...]] = {}
    ancestors_by_graph: dict[int, tuple[NodePath, ...]] = {}

    def resolve_ancestors(graph_index: int) -> None:
        scopes: list[GraphScope] = []
        ancestors: list[NodePath] = []
        current = graph_index
        while (owner := graph_owners[current]) is not None:
            owner_index = declaration_to_compiled[owner]
            parent_graph = node_graphs[owner_index]
            scopes.append(GraphScope(node_paths=graph_node_paths[parent_graph]))
            ancestors.append(step_paths[owner_index])
            current = parent_graph
        scopes_by_graph[graph_index] = (*scopes, *external_ancestor_graph_scopes)
        ancestors_by_graph[graph_index] = tuple(ancestors)

    predicates = native.predicates()
    condition_reads = native.condition_reads()
    compiled_conditions = tuple(
        CompiledRuntimeCondition(
            when=cast("ConditionalEdgePredicate", predicate),
            inputs=tuple(refs_by_declaration[read] for read in reads),
        )
        for predicate, reads in zip(predicates, condition_reads, strict=True)
    )
    execution_after = native.graph.all_prerequisites()
    execution_conditions = native.graph.node_conditions()
    node_inputs = native.node_inputs()

    def prerequisite(value: tuple[int, int | None]) -> CompiledPrerequisite:
        node, condition = value
        ref = refs_by_declaration[active_declarations[node]]
        if condition is None:
            return ref
        return CompiledConditionalDependency(
            node=ref,
            condition=compiled_conditions[condition],
        )

    steps: list[CompiledStep] = []
    composites: list[CompiledComposite] = []
    ordered_nodes: list[CompiledNode] = []
    for compiled_index, declaration in enumerate(active_declarations):
        graph_index = node_graphs[compiled_index]
        if graph_index not in scopes_by_graph:
            resolve_ancestors(graph_index)
        context = CompiledNodeContext(
            node_id=step_ids[compiled_index],
            graph_node_paths=graph_node_paths[graph_index],
            ancestor_graph_scopes=scopes_by_graph[graph_index],
            ancestor_composite_ids=ancestors_by_graph[graph_index],
        )
        owner_declaration = graph_owners[graph_index]
        owner = (
            None
            if owner_declaration is None
            else _require_composite_ref(refs_by_declaration[owner_declaration])
        )
        after = tuple(prerequisite(value) for value in execution_after[compiled_index])
        condition_index = execution_conditions[compiled_index]
        condition = (
            None if condition_index is None else compiled_conditions[condition_index]
        )
        ref = refs_by_declaration[declaration]
        child_graph = child_graphs[compiled_index]
        if child_graph is None:
            action = actions[compiled_index]
            compiled_step: CompiledStep
            if action is None:
                compiled_step = CompiledRustStep(
                    ref=cast("CompiledStepRef", ref),
                    after=after,
                    condition=condition,
                    owner=owner,
                    context=context,
                    inputs=tuple(
                        refs_by_declaration[node]
                        for node in node_inputs[compiled_index]
                    ),
                )
            else:
                compiled_step = CompiledPythonStep(
                    ref=cast("CompiledStepRef", ref),
                    action=cast("Step", action),
                    after=after,
                    condition=condition,
                    owner=owner,
                    context=context,
                    inputs=tuple(
                        refs_by_declaration[node]
                        for node in node_inputs[compiled_index]
                    ),
                )
            steps.append(compiled_step)
            ordered_nodes.append(compiled_step)
            continue
        component_source_graph = cast(
            "GraphStructure | RustGraphStructure",
            source_graphs[child_graph],
        )
        composite = CompiledComposite(
            ref=_require_composite_ref(ref),
            source_graph=component_source_graph,
            graph_label=graph_labels[child_graph],
            children=tuple(
                refs_by_declaration[child]
                for child in graph_local_nodes[child_graph]
                if child in declaration_to_compiled
            ),
            after=after,
            condition=condition,
            owner=owner,
            observation=(
                component_source_graph.observation
                if isinstance(component_source_graph, GraphStructure)
                else GraphObservationMessages()
            ),
            context=context,
            graph_node_paths=graph_node_paths[child_graph],
        )
        composites.append(composite)
        ordered_nodes.append(composite)

    roots = tuple(
        refs_by_declaration[active_declarations[index]]
        for index in native.graph.roots()
    )
    prefix, disabled_edges, condition_origins = native.export_origins()
    return GraphCompilation(
        source_graph=source_graph,
        label=graph_labels[0],
        steps=tuple(steps),
        composites=tuple(composites),
        roots=roots,
        root_label_path=graph_label_paths[0],
        root_graph_node_paths=graph_node_paths[0],
        _structure_origins=CompilationOrigins(
            parent_node_path=NodePath(tuple(prefix)) if prefix else None,
            disabled_edges=tuple(
                (None if owner is None else NodePath(tuple(owner)), index)
                for owner, index in disabled_edges
            ),
            conditions=tuple(
                NodeConditionOrigin(NodePath(tuple(target)))
                if edge_index is None
                else EdgeConditionOrigin(NodePath(tuple(target)), edge_index)
                for target, edge_index in condition_origins
            ),
        ),
        _execution_graph_input=_PrecompiledExecutionGraph(
            graph=native.graph,
            conditions=compiled_conditions,
            nodes=tuple(ordered_nodes),
            rust_actions=native.rust_actions(),
        ),
    )


def _require_composite_ref(ref: CompiledNodeRef) -> CompiledCompositeRef:
    """要求结构引用指向 composite，避免用可选 mode 字段表达节点类别。"""

    if not isinstance(ref, CompiledCompositeRef):
        raise GraphError(f"compiled graph expected a composite ref: {ref.path}")
    return ref
