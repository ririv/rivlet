"""Public graph-model declarations, assembled from their owning modules."""

from .._native import NativeGraph
from .condition import (
    ConditionalEdgePredicate,
)
from .condition import _RuntimeCondition as _RuntimeCondition
from .edge import (
    ConditionalPrerequisite,
    Edge,
    GraphEdge,
    prerequisite_step,
)
from .edge import _condition_uses as _condition_uses
from .graph import (
    Graph,
    GraphMode,
    GraphObservationMessages,
)
from .identity import StepId, StepPath
from .leaf import Leaf, LeafCallable
from .step import (
    Step,
    StepLabelPath,
    resolve_step_label,
    step,
    step_paths_for_mount,
)
from .wiring import (
    GraphWiring,
    Results,
    StepWiring,
)

# 跨模块的联合类型在装配入口定义一次。此时真实声明类型已加载，
# 类型检查和运行时读取 TypeAliasType.__value__ 使用同一组类型身份。
type Node = Leaf | Graph | NativeGraph
type NodeInput = Node | LeafCallable
type ConditionInput = (
    ConditionalEdgePredicate | tuple[ConditionalEdgePredicate, Step, *tuple[Step, ...]]
)
type StepPrerequisite = Step | ConditionalPrerequisite
type GraphWiringOperand = Step | Leaf | Graph | GraphWiring
type GraphWiringInput = tuple[StepWiring, ...] | GraphWiring

__all__ = [
    "StepId",
    "StepPath",
    "step_paths_for_mount",
    "StepLabelPath",
    "GraphMode",
    "GraphObservationMessages",
    "LeafCallable",
    "Leaf",
    "Node",
    "NodeInput",
    "ConditionalEdgePredicate",
    "ConditionInput",
    "ConditionalPrerequisite",
    "StepPrerequisite",
    "prerequisite_step",
    "Step",
    "resolve_step_label",
    "step",
    "StepWiring",
    "Results",
    "Edge",
    "GraphEdge",
    "GraphWiring",
    "GraphWiringOperand",
    "GraphWiringInput",
    "Graph",
]
