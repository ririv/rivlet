//! Host-side result access; never restored as an action-context query API.
use crate::{ExecutionOutcome, NodeFailure, NodeOutcome};
use action::ActionError;
use graph::{LookupError, OutputEndpoint};
use rivlet::{BlockCause, NodeId};
use std::sync::Arc;

#[derive(Debug)]
pub enum ResultLookupError {
    Reference(LookupError),
    Failed { node: NodeId, failure: NodeFailure },
    Blocked { node: NodeId, cause: BlockCause },
    Cancelled(NodeId),
    Value { node: NodeId, error: ActionError },
}
impl std::fmt::Display for ResultLookupError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Reference(error) => std::fmt::Display::fmt(error, f),
            Self::Failed { node, .. } => write!(
                f,
                "node {} failed; it has no successful result",
                node.index()
            ),
            Self::Blocked { node, .. } => write!(
                f,
                "node {} is blocked; it has no successful result",
                node.index()
            ),
            Self::Cancelled(node) => write!(
                f,
                "node {} was cancelled; it has no successful result",
                node.index()
            ),
            Self::Value { node, error } => write!(f, "result of node {}: {error}", node.index()),
        }
    }
}
impl std::error::Error for ResultLookupError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Reference(error) => Some(error),
            Self::Value { error, .. }
            | Self::Failed {
                failure: NodeFailure::Action(error),
                ..
            } => Some(error),
            Self::Failed { .. } | Self::Blocked { .. } | Self::Cancelled(_) => None,
        }
    }
}
impl ExecutionOutcome {
    /// Reads a successful value through an actual node or graph output reference.
    /// Only skipped producers return None; successful unit values remain Some.
    ///
    /// # Errors
    /// Rejects absent/ambiguous references, failed/blocked/cancelled nodes and wrong types.
    ///
    /// # Panics
    /// Panics on an internal compilation/outcome alignment bug. Public constructors
    /// always bind outcomes to the exact compilation that produced their slots.
    pub fn result<T: Send + Sync + 'static>(
        &self,
        source: impl Into<OutputEndpoint>,
    ) -> Result<Option<Arc<T>>, ResultLookupError> {
        let node = self
            .compilation()
            .result_index(source)
            .map_err(ResultLookupError::Reference)?;
        match self
            .node(node)
            .expect("resolved result belongs to this outcome's compilation")
        {
            NodeOutcome::Succeeded(value) => value
                .downcast::<T>()
                .map(Some)
                .map_err(|error| ResultLookupError::Value { node, error }),
            NodeOutcome::Skipped => Ok(None),
            NodeOutcome::Failed(failure) => Err(ResultLookupError::Failed {
                node,
                failure: failure.clone(),
            }),
            NodeOutcome::Blocked(cause) => Err(ResultLookupError::Blocked {
                node,
                cause: *cause,
            }),
            NodeOutcome::Cancelled => Err(ResultLookupError::Cancelled(node)),
        }
    }
}
