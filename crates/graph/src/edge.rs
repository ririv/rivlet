use rivlet::NodeIdentity;

use crate::{InputPort, Node, OutputPort};

/// A completion dependency or a directed named-port connection.
#[derive(Clone, Debug)]
pub enum Edge {
    Completion {
        source: NodeIdentity,
        target: NodeIdentity,
    },
    Ports {
        source: OutputPort,
        target: InputPort,
    },
}

impl Edge {
    #[must_use]
    pub fn new(source: &(impl Node + ?Sized), target: &(impl Node + ?Sized)) -> Self {
        Self::Completion {
            source: source.id(),
            target: target.id(),
        }
    }

    #[must_use]
    pub const fn ports(source: OutputPort, target: InputPort) -> Self {
        Self::Ports { source, target }
    }

    #[must_use]
    pub const fn source(&self) -> NodeIdentity {
        match self {
            Self::Completion { source, .. } => *source,
            Self::Ports { source, .. } => source.owner(),
        }
    }
    #[must_use]
    pub const fn target(&self) -> NodeIdentity {
        match self {
            Self::Completion { target, .. } => *target,
            Self::Ports { target, .. } => target.owner(),
        }
    }
}
