use std::error::Error;
use std::fmt::{self, Display, Formatter};

use action::ActionError;
use rivlet::{NodeId, SessionError};

/// Infrastructure failures, distinct from action failures in an execution outcome.
#[derive(Debug)]
pub enum ExecutionError {
    InvalidWorkers,
    Session(SessionError),
    Executor(ActionError),
    UnsupportedConditions,
    UnexpectedComposite(NodeId),
    MissingResult(NodeId),
    DuplicateOutcome(NodeId),
    InvalidTransition(NodeId),
    InvalidCompletion,
    Stalled,
}

impl Display for ExecutionError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidWorkers => formatter.write_str("workers must be greater than zero"),
            Self::Session(error) => Display::fmt(error, formatter),
            Self::Executor(error) => Display::fmt(error, formatter),
            Self::UnsupportedConditions => {
                formatter.write_str("Rust predicate execution is not supported")
            }
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
            Self::Stalled => formatter.write_str("execution has no running action to wait for"),
        }
    }
}

impl Error for ExecutionError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Session(error) => Some(error),
            Self::Executor(error) => Some(error),
            _ => None,
        }
    }
}

impl From<SessionError> for ExecutionError {
    fn from(error: SessionError) -> Self {
        Self::Session(error)
    }
}
