use std::error::Error;
use std::fmt::{self, Display, Formatter};

use rivlet::NodeIdentity;

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum GraphError {
    EmptyNodeLabel,
    DuplicateNode(NodeIdentity),
    UnknownNode(NodeIdentity),
    ExpectedGraph(NodeIdentity),
    InvalidPortName(String),
    DuplicatePort(String),
    EmptyInputPort(String),
    DuplicateInput(String),
    UnknownInputPort(String),
    UnknownOutputPort(String),
    EmptyResults,
    DuplicateResults(NodeIdentity),
    ResultTargetNotStep(NodeIdentity),
    ResultInputCount {
        node: NodeIdentity,
        expected: usize,
        actual: usize,
    },
    ResultTypeMismatch {
        expected: &'static str,
        actual: &'static str,
    },
    Topology(rivlet::TopologyError),
    Declaration(rivlet::DeclarationError),
}

impl Display for GraphError {
    fn fmt(&self, f: &mut Formatter<'_>) -> fmt::Result {
        match self {
            Self::EmptyNodeLabel => f.write_str("node label must not be empty"),
            Self::DuplicateNode(id) => {
                write!(f, "node {} occurs twice in one structure", id.value())
            }
            Self::UnknownNode(id) => write!(
                f,
                "node {} is outside this structure; use a graph port for descendants",
                id.value()
            ),
            Self::ExpectedGraph(id) => write!(f, "node {} has no graph interface", id.value()),
            Self::InvalidPortName(name) => write!(f, "invalid port name: {name:?}"),
            Self::DuplicatePort(name) => write!(f, "duplicate port: {name}"),
            Self::EmptyInputPort(name) => write!(f, "input port has no consumers: {name}"),
            Self::DuplicateInput(name) => write!(f, "input port repeats a consumer: {name}"),
            Self::UnknownInputPort(name) => write!(f, "unknown input port: {name}"),
            Self::UnknownOutputPort(name) => write!(f, "unknown output port: {name}"),
            Self::EmptyResults => f.write_str("result connections require at least one source"),
            Self::DuplicateResults(id) => {
                write!(f, "node {} has more than one result group", id.value())
            }
            Self::ResultTargetNotStep(id) => {
                write!(f, "result target {} is not a leaf", id.value())
            }
            Self::ResultInputCount {
                node,
                expected,
                actual,
            } => write!(
                f,
                "node {} expects {expected} result inputs, received {actual}",
                node.value()
            ),
            Self::ResultTypeMismatch { expected, actual } => write!(
                f,
                "result type mismatch: expected {expected}, received {actual}"
            ),
            Self::Topology(error) => Display::fmt(error, f),
            Self::Declaration(error) => Display::fmt(error, f),
        }
    }
}

impl Error for GraphError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Topology(error) => Some(error),
            Self::Declaration(error) => Some(error),
            _ => None,
        }
    }
}
