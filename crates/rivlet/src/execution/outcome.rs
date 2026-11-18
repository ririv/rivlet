use std::sync::Arc;

use crate::action::{ActionError, Value};
use crate::graph::Compilation;
use rivlet_core::{BlockCause, ConditionId, NodeId};

/// One direct failure witness; child failures retain their compiled node identity.
#[derive(Debug)]
pub enum NodeFailure {
    Action(ActionError),
    Child(NodeId),
}

/// A terminal node outcome, with a value only for successful completion.
#[derive(Debug)]
pub enum NodeOutcome {
    Succeeded(Value),
    Skipped,
    Failed(NodeFailure),
    Blocked(BlockCause),
}

/// A condition is either never requested, resolved, or failed with its original error.
#[derive(Debug)]
pub enum ConditionOutcome {
    NotEvaluated,
    Resolved(bool),
    Failed(ActionError),
}

/// The final status of an execution, never an intermediate Running state.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ExecutionStatus {
    Succeeded,
    Failed,
}

/// Immutable terminal outcomes bound to the exact compilation that produced them.
pub struct ExecutionOutcome {
    compilation: Arc<Compilation>,
    nodes: Vec<NodeOutcome>,
    conditions: Vec<ConditionOutcome>,
}

impl ExecutionOutcome {
    pub(crate) fn new(
        compilation: Arc<Compilation>,
        nodes: Vec<NodeOutcome>,
        conditions: Vec<ConditionOutcome>,
    ) -> Self {
        Self {
            compilation,
            nodes,
            conditions,
        }
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
        if self.compilation.graph().roots().iter().any(|root| {
            matches!(
                self.nodes[root.index()],
                NodeOutcome::Failed(_) | NodeOutcome::Blocked(_)
            )
        }) {
            ExecutionStatus::Failed
        } else {
            ExecutionStatus::Succeeded
        }
    }
}
