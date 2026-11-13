from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import static as static
    from ._native import NativeGraph
    from .compile import (
        CompiledComposite,
        CompiledCompositeRef,
        CompiledConditionalDependency,
        CompiledNativeStep,
        CompiledRuntimeCondition,
        CompiledStep,
        CompiledStepRef,
        GraphCompilation,
        compile_graph,
    )
    from .errors import GraphError
    from .events import (
        ActionFailed,
        BlockedByCondition,
        BlockedByConditionInput,
        BlockedByDependency,
        BlockedByParent,
        ConditionSite,
        EdgeConditionSite,
        EventDeliveryError,
        NodeConditionSite,
        StepBlockCause,
        StepEventSink,
        StepExecutionEvent,
        StepExecutionState,
        StepExecutionStateDetail,
        StepFailureCause,
        SubgraphFailed,
    )
    from .execution import Execution
    from .execution_output import (
        NO_EXECUTION_OUTPUT,
        ExecutionOutput,
        NoExecutionOutput,
        TreeFileExecutionOutput,
    )
    from .interface import (
        AnyProviderRequirement,
        ExactProviderRequirement,
        GraphInterface,
        GraphInterfaceInput,
        PortRequirement,
        ProviderSelection,
        resolve_port_requirements,
    )
    from .model import (
        ConditionalPrerequisite,
        ConditionInput,
        Edge,
        Graph,
        GraphEdge,
        GraphMode,
        GraphObservationMessages,
        GraphWiring,
        Leaf,
        LeafCallable,
        Node,
        NodeInput,
        Step,
        StepId,
        StepLabelPath,
        StepPath,
        StepWiring,
        step,
    )
    from .observe import GraphObservationEvent, GraphObserver
    from .option import Nothing, Option, Some
    from .render import (
        GraphRenderFormat,
        GraphRenderOptions,
        render_execution,
        render_execution_structure,
        render_graph,
        render_graph_compilation_mermaid,
        render_graph_mermaid,
        render_live_execution,
        resolve_graph_render_options,
    )
    from .results import (
        GraphScope,
        ResultStore,
        ResultView,
        StepControl,
        StepRunContext,
    )
    from .runtime import (
        NO_ACTION_LOG,
        ActionLog,
        AsyncRunOptions,
        ExecutionControl,
        ExecutionState,
        ExecutionStopped,
        GraphNodeFailure,
        NoActionLog,
        RunOptions,
        SerialGraphExecutor,
        ThreadRunOptions,
    )
    from .selection import (
        MultipleStaticBranchesSelectedError,
        NoStaticBranchSelectedError,
        StaticBranch,
        StaticSelectionError,
        select_one,
    )
    from .static import Case, StaticSelection
    from .static_condition import (
        StaticCondition,
        StaticConditionEval,
        StaticConditionEvalFn,
        StaticConditionEvaluator,
        all_,
        any_,
        evaluate_static_condition,
        not_,
    )
    from .static_conditions import StaticConditions
    from .structure import (
        CompilationDocument,
        DeclarationDocument,
        GraphDocument,
        export_graph,
    )
    from .topology import Arc, DAGTopology, DirectedTopology, TopologyCycleError

_EXPORT_GROUPS = (
    (
        ".structure",
        ("CompilationDocument", "DeclarationDocument", "GraphDocument", "export_graph"),
    ),
    (".static", ("Case", "StaticSelection")),
    (".static_conditions", ("StaticConditions",)),
    ("._native", ("NativeGraph",)),
    (
        ".events",
        (
            "ActionFailed",
            "BlockedByCondition",
            "BlockedByConditionInput",
            "BlockedByDependency",
            "BlockedByParent",
            "ConditionSite",
            "EdgeConditionSite",
            "NodeConditionSite",
            "SubgraphFailed",
            "StepFailureCause",
            "StepBlockCause",
            "StepExecutionStateDetail",
            "EventDeliveryError",
            "StepEventSink",
            "StepExecutionEvent",
            "StepExecutionState",
        ),
    ),
    (
        ".interface",
        (
            "GraphInterfaceInput",
            "GraphInterface",
            "PortRequirement",
            "AnyProviderRequirement",
            "ExactProviderRequirement",
            "ProviderSelection",
            "resolve_port_requirements",
        ),
    ),
    (
        ".compile",
        (
            "CompiledStep",
            "CompiledNativeStep",
            "CompiledStepRef",
            "CompiledComposite",
            "CompiledCompositeRef",
            "CompiledRuntimeCondition",
            "CompiledConditionalDependency",
            "GraphCompilation",
            "compile_graph",
        ),
    ),
    (".errors", ("GraphError",)),
    (
        ".execution_output",
        (
            "ExecutionOutput",
            "NoExecutionOutput",
            "NO_EXECUTION_OUTPUT",
            "TreeFileExecutionOutput",
        ),
    ),
    (
        ".model",
        (
            "Graph",
            "GraphMode",
            "GraphObservationMessages",
            "GraphWiring",
            "GraphEdge",
            "Edge",
            "ConditionalPrerequisite",
            "ConditionInput",
            "Step",
            "Node",
            "NodeInput",
            "step",
            "StepWiring",
            "Leaf",
            "LeafCallable",
            "StepId",
            "StepPath",
            "StepLabelPath",
        ),
    ),
    (".observe", ("GraphObservationEvent", "GraphObserver")),
    (".execution", ("Execution",)),
    (
        ".render",
        (
            "render_graph",
            "render_execution",
            "render_live_execution",
            "render_execution_structure",
            "resolve_graph_render_options",
            "render_graph_mermaid",
            "render_graph_compilation_mermaid",
            "GraphRenderFormat",
            "GraphRenderOptions",
        ),
    ),
    (".option", ("Some", "Nothing", "Option")),
    (
        ".results",
        (
            "ResultStore",
            "ResultView",
            "GraphScope",
            "StepControl",
            "StepRunContext",
        ),
    ),
    (
        ".runtime",
        (
            "ExecutionState",
            "ExecutionControl",
            "ExecutionStopped",
            "GraphNodeFailure",
            "SerialGraphExecutor",
            "RunOptions",
            "ActionLog",
            "NoActionLog",
            "NO_ACTION_LOG",
            "ThreadRunOptions",
            "AsyncRunOptions",
        ),
    ),
    (
        ".selection",
        (
            "StaticBranch",
            "StaticSelectionError",
            "NoStaticBranchSelectedError",
            "MultipleStaticBranchesSelectedError",
            "select_one",
        ),
    ),
    (
        ".static_condition",
        (
            "StaticCondition",
            "StaticConditionEval",
            "StaticConditionEvalFn",
            "StaticConditionEvaluator",
            "all_",
            "any_",
            "evaluate_static_condition",
            "not_",
        ),
    ),
    (
        ".topology",
        ("Arc", "DirectedTopology", "DAGTopology", "TopologyCycleError"),
    ),
)

__all__ = [
    "CompilationDocument",
    "DeclarationDocument",
    "GraphDocument",
    "export_graph",
    "static",
    "Case",
    "StaticSelection",
    "EventDeliveryError",
    "StepEventSink",
    "ActionFailed",
    "BlockedByCondition",
    "BlockedByConditionInput",
    "BlockedByDependency",
    "BlockedByParent",
    "ConditionSite",
    "EdgeConditionSite",
    "NodeConditionSite",
    "SubgraphFailed",
    "StepFailureCause",
    "StepBlockCause",
    "StepExecutionStateDetail",
    "StepExecutionEvent",
    "StepExecutionState",
    "GraphInterfaceInput",
    "GraphInterface",
    "PortRequirement",
    "AnyProviderRequirement",
    "ExactProviderRequirement",
    "ProviderSelection",
    "resolve_port_requirements",
    "Graph",
    "GraphError",
    "GraphMode",
    "GraphObservationMessages",
    "GraphWiring",
    "GraphEdge",
    "Edge",
    "ConditionalPrerequisite",
    "ConditionInput",
    "StaticCondition",
    "StaticConditions",
    "StaticConditionEval",
    "StaticConditionEvalFn",
    "StaticConditionEvaluator",
    "all_",
    "any_",
    "evaluate_static_condition",
    "not_",
    "Step",
    "Node",
    "NodeInput",
    "step",
    "StepWiring",
    "Leaf",
    "LeafCallable",
    "NativeGraph",
    "StepId",
    "StepPath",
    "StepLabelPath",
    "CompiledStep",
    "CompiledNativeStep",
    "CompiledStepRef",
    "CompiledComposite",
    "CompiledCompositeRef",
    "CompiledRuntimeCondition",
    "CompiledConditionalDependency",
    "GraphCompilation",
    "compile_graph",
    "render_graph",
    "render_execution",
    "render_live_execution",
    "render_execution_structure",
    "resolve_graph_render_options",
    "render_graph_mermaid",
    "render_graph_compilation_mermaid",
    "GraphRenderFormat",
    "GraphRenderOptions",
    "Execution",
    "ExecutionOutput",
    "NoExecutionOutput",
    "NO_EXECUTION_OUTPUT",
    "TreeFileExecutionOutput",
    "ExecutionState",
    "ExecutionControl",
    "ExecutionStopped",
    "GraphNodeFailure",
    "SerialGraphExecutor",
    "RunOptions",
    "ActionLog",
    "NoActionLog",
    "NO_ACTION_LOG",
    "ThreadRunOptions",
    "AsyncRunOptions",
    "StaticBranch",
    "StaticSelectionError",
    "NoStaticBranchSelectedError",
    "MultipleStaticBranchesSelectedError",
    "select_one",
    "ResultStore",
    "ResultView",
    "GraphScope",
    "StepControl",
    "Some",
    "Nothing",
    "Option",
    "StepRunContext",
    "GraphObservationEvent",
    "GraphObserver",
    "Arc",
    "DirectedTopology",
    "DAGTopology",
    "TopologyCycleError",
]
_EXPORT_MODULES = {
    name: module_name for module_name, names in _EXPORT_GROUPS for name in names
}


def __getattr__(name: str) -> object:
    """Load only the graph module that owns the requested public symbol."""

    if name == "static":
        module = import_module(".static", __name__)
        globals()[name] = module
        return module
    try:
        module_name = _EXPORT_MODULES[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    module = import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose lazy public symbols to interactive API discovery."""

    return sorted({*globals(), *__all__})
