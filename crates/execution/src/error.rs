use std::error::Error;
use std::fmt::{self, Display, Formatter};

use action::ActionError;
use rivlet::{ConditionId, NodeId, SessionError};

/// Infrastructure failures, distinct from action failures in an execution outcome.
#[derive(Debug)]
pub enum ExecutionError {
    Concurrency(crate::ConcurrencyError),
    InvalidWorkers,
    AsyncPredicate(ConditionId),
    AsyncAction(NodeId),
    Session(SessionError),
    Executor(ActionError),
    UnexpectedComposite(NodeId),
    MissingResult(NodeId),
    DuplicateOutcome(NodeId),
    InvalidTransition(NodeId),
    InvalidCompletion,
    Stalled,
    Observation {
        error: ActionError,
        outcome: Box<crate::ExecutionOutcome>,
    },
}

impl Display for ExecutionError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        match self {
            Self::AsyncAction(id) => write!(
                formatter,
                "action {} is asynchronous; use run_async()",
                id.index()
            ),
            Self::Concurrency(error) => Display::fmt(error, formatter),
            Self::Observation { error, .. } => {
                write!(formatter, "execution event delivery failed: {error}")
            }
            Self::AsyncPredicate(id) => write!(
                formatter,
                "condition {} is asynchronous; use run_async()",
                id.index()
            ),
            Self::InvalidWorkers => formatter.write_str("workers must be greater than zero"),
            Self::Session(error) => Display::fmt(error, formatter),
            Self::Executor(error) => Display::fmt(error, formatter),
            Self::UnexpectedComposite(node) => write!(
                formatter,
                "composite {} was dispatched as an action",
                node.index()
            ),
            Self::MissingResult(node) => {
                write!(formatter, "node {} has no recorded result", node.index())
            }
            Self::DuplicateOutcome(node) => {
                write!(formatter, "node {} already has an outcome", node.index())
            }
            Self::InvalidTransition(node) => {
                write!(
                    formatter,
                    "node {} has an inconsistent outcome",
                    node.index()
                )
            }
            Self::InvalidCompletion => {
                formatter.write_str("completion count exceeds running action count")
            }
            Self::Stalled => formatter.write_str("execution has no running work to wait for"),
        }
    }
}

impl Error for ExecutionError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Concurrency(error) => Some(error),
            Self::Observation { error, .. } | Self::Executor(error) => Some(error),
            Self::Session(error) => Some(error),
            _ => None,
        }
    }
}

impl From<crate::ConcurrencyError> for ExecutionError {
    fn from(error: crate::ConcurrencyError) -> Self {
        Self::Concurrency(error)
    }
}

impl From<SessionError> for ExecutionError {
    fn from(error: SessionError) -> Self {
        Self::Session(error)
    }
}
