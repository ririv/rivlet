use std::sync::Arc;

use action::{ActionError, Value};
use graph::Compilation;
use rivlet::{BlockCause, ConditionId, NodeId};

/// One direct failure witness; child failures retain their compiled node identity.
#[derive(Clone, Debug)]
pub enum NodeFailure {
    Action(ActionError),
    Child(NodeId),
    Expansion(graph::GraphError),
    AsyncSubgraph,
    Subgraph,
}

/// A terminal node outcome, with a value only for successful completion.
#[derive(Debug)]
pub enum NodeOutcome {
    Succeeded(Value),
    Skipped,
    Cancelled,
    Failed(NodeFailure),
    Blocked(BlockCause),
}

/// A condition is either never requested, resolved, or failed with its original error.
#[derive(Debug)]
pub enum ConditionOutcome {
    NotEvaluated,
    Cancelled,
    Resolved(bool),
    Failed(ActionError),
}

/// The final status of an execution, never an intermediate Running state.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ExecutionStatus {
    Succeeded,
    Failed,
    Cancelled,
}

/// Immutable terminal outcomes bound to the exact compilation that produced them.
pub struct ExecutionOutcome {
    compilation: Arc<Compilation>,
    nodes: Vec<NodeOutcome>,
    conditions: Vec<ConditionOutcome>,
    status: ExecutionStatus,
    subgraphs: std::collections::HashMap<NodeId, ExecutionOutcome>,
}

impl ExecutionOutcome {
    pub(crate) fn delivered(
        self,
        error: Option<ActionError>,
    ) -> Result<Self, crate::ExecutionError> {
        match error {
            None => Ok(self),
            Some(error) => Err(crate::ExecutionError::Observation {
                error,
                outcome: Box::new(self),
            }),
        }
    }
    pub(crate) fn new(
        compilation: Arc<Compilation>,
        nodes: Vec<NodeOutcome>,
        conditions: Vec<ConditionOutcome>,
        status: ExecutionStatus,
    ) -> Self {
        Self {
            compilation,
            nodes,
            conditions,
            status,
            subgraphs: std::collections::HashMap::new(),
        }
    }
    pub(crate) fn with_subgraphs(
        mut self,
        subgraphs: std::collections::HashMap<NodeId, Self>,
    ) -> Self {
        self.subgraphs = subgraphs;
        self
    }
    /// Returns the generated child execution, bound to its own compilation.
    #[must_use]
    pub fn subgraph(&self, generator: NodeId) -> Option<&Self> {
        self.subgraphs.get(&generator)
    }

    #[must_use]
    pub fn compilation(&self) -> &Compilation {
        &self.compilation
    }

    /// Returns None only for an index outside this compilation.
    #[must_use]
    pub fn node(&self, node: NodeId) -> Option<&NodeOutcome> {
        self.nodes.get(node.index())
    }

    /// Returns None only for an index outside this compilation.
    #[must_use]
    pub fn condition(&self, condition: ConditionId) -> Option<&ConditionOutcome> {
        self.conditions.get(condition.index())
    }

    /// Iterates mounted execution identities, not reusable declaration identities.
    #[must_use]
    pub fn nodes(&self) -> impl ExactSizeIterator<Item = (NodeId, &NodeOutcome)> {
        self.nodes
            .iter()
            .enumerate()
            .map(|(index, outcome)| (NodeId::new(index), outcome))
    }

    #[must_use]
    pub fn status(&self) -> ExecutionStatus {
        self.status
    }
}

impl std::fmt::Debug for ExecutionOutcome {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ExecutionOutcome")
            .field("status", &self.status)
            .field("nodes", &self.nodes)
            .field("conditions", &self.conditions)
            .finish_non_exhaustive()
    }
}
