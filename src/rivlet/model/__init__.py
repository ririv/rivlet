"""Public graph-model declarations, assembled from their owning modules."""

from .condition import (
    ConditionalEdgePredicate,
)
from .condition import _RuntimeCondition as _RuntimeCondition
from .edge import (
    ConditionalPrerequisite,
    Edge,
    GraphEdge,
    prerequisite_node,
)
from .edge import _condition_uses as _condition_uses
from .graph import (
    Graph,
    GraphMode,
    GraphObservationMessages,
    GraphStructure,
)
from .identity import NodeId, NodePath
from .node import (
    Node,
    NodeLabelPath,
    node_paths_for_mount,
    resolve_node_label,
)
from .step import Step, StepCallable, step
from .wiring import (
    GraphWiring,
    NodeWiring,
    Results,
)

# 跨模块的联合类型在装配入口定义一次。此时真实声明类型已加载，
# 类型检查和运行时读取 TypeAliasType.__value__ 使用同一组类型身份。
type NodeKind = Step | Graph
type ConditionInput = (
    ConditionalEdgePredicate | tuple[ConditionalEdgePredicate, Node, *tuple[Node, ...]]
)
type NodePrerequisite = Node | ConditionalPrerequisite
type GraphWiringOperand = NodeKind | GraphWiring
type GraphWiringInput = tuple[NodeWiring, ...] | GraphWiring

__all__ = [
    "NodeId",
    "NodePath",
    "node_paths_for_mount",
    "NodeLabelPath",
    "GraphMode",
    "GraphObservationMessages",
    "StepCallable",
    "Step",
    "Node",
    "NodeKind",
    "ConditionalEdgePredicate",
    "ConditionInput",
    "ConditionalPrerequisite",
    "NodePrerequisite",
    "prerequisite_node",
    "resolve_node_label",
    "step",
    "NodeWiring",
    "Results",
    "Edge",
    "GraphEdge",
    "GraphWiring",
    "GraphWiringOperand",
    "GraphWiringInput",
    "Graph",
    "GraphStructure",
]
