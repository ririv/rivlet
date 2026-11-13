//! Pure graph models and algorithms used by every language adapter.

pub mod declaration;
pub mod execution;
pub mod failure;
pub mod identity;
pub mod lowering;
pub mod ports;
pub mod session;

pub use declaration::{
    DeclarationError, DeclarationNode, DeclarationPrerequisite, GraphCompilation, GraphDeclaration,
    NodeActivation,
};
pub use execution::{
    CompiledExecutionGraph, ConditionId, ExecutionGraphError, ExecutionNode, ExecutionNodeKind,
    PortConnection, Prerequisite, RuntimeCondition,
};
pub use failure::{BlockCause, ConditionSite, FailureCause};
pub use identity::NodeIdentity;
pub use lowering::{
    DeclaredGraph, DeclaredNode, DeclaredNodeKind, ExecutionGraphDeclaration, GraphId,
    LoweringError,
};
pub use ports::{
    ComponentId, PortId, PortRequirement, PortResolutionError, ProviderSelection, RequirementId,
    ResolvedPortConnection, resolve_ports,
};
pub use session::{
    ConditionRequest, ConditionResolution, ExecutionBatch, ExecutionEvent, ExecutionSession,
    ExecutionUpdate, NodeState, SessionError, SessionStatus,
};
pub use topology::{Arc, DagTopology, DirectedTopology, NodeId, TopologyError};
