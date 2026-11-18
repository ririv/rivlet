use rivlet_core::NodeIdentity;

use super::{Condition, GraphError, InputPort, Node, OutputEndpoint, OutputPort, Step};

/// A nonempty immutable group, constructed only by [`Edge::results`].
#[derive(Clone, Debug)]
pub struct ResultSources(Vec<OutputEndpoint>);

impl std::ops::Deref for ResultSources {
    type Target = [OutputEndpoint];
    fn deref(&self) -> &Self::Target {
        &self.0
    }
}

/// A callable step or a graph-owned input port, never an unqualified graph.
#[derive(Clone, Debug)]
pub enum ResultTarget {
    Step(NodeIdentity),
    Port(InputPort),
}

impl From<&Step> for ResultTarget {
    fn from(step: &Step) -> Self {
        Self::Step(step.id())
    }
}
impl From<InputPort> for ResultTarget {
    fn from(port: InputPort) -> Self {
        Self::Port(port)
    }
}
impl ResultTarget {
    #[must_use]
    pub const fn owner(&self) -> NodeIdentity {
        match self {
            Self::Step(id) => *id,
            Self::Port(port) => port.owner(),
        }
    }
}

/// A completion dependency or a directed named-port connection.
#[derive(Clone, Debug)]
pub enum Edge {
    Completion {
        source: NodeIdentity,
        target: NodeIdentity,
    },
    ConditionalCompletion {
        source: NodeIdentity,
        target: NodeIdentity,
        condition: Condition,
    },
    Ports {
        source: OutputPort,
        target: InputPort,
    },
    ConditionalPorts {
        source: OutputPort,
        target: InputPort,
        condition: Condition,
    },
    Results {
        sources: ResultSources,
        target: ResultTarget,
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
    pub fn with_condition(
        source: &(impl Node + ?Sized),
        target: &(impl Node + ?Sized),
        condition: Condition,
    ) -> Self {
        Self::ConditionalCompletion {
            source: source.id(),
            target: target.id(),
            condition,
        }
    }

    #[must_use]
    pub const fn ports_with_condition(
        source: OutputPort,
        target: InputPort,
        condition: Condition,
    ) -> Self {
        Self::ConditionalPorts {
            source,
            target,
            condition,
        }
    }

    #[must_use]
    pub const fn condition(&self) -> Option<&Condition> {
        match self {
            Self::ConditionalCompletion { condition, .. }
            | Self::ConditionalPorts { condition, .. } => Some(condition),
            Self::Completion { .. } | Self::Ports { .. } | Self::Results { .. } => None,
        }
    }

    /// Declares one complete ordered result group and its execution dependencies.
    ///
    /// # Errors
    /// Returns an error for an empty source group.
    pub fn results<T: Into<OutputEndpoint>>(
        sources: impl IntoIterator<Item = T>,
        target: impl Into<ResultTarget>,
    ) -> Result<Self, GraphError> {
        let sources: Vec<_> = sources.into_iter().map(Into::into).collect();
        if sources.is_empty() {
            return Err(GraphError::EmptyResults);
        }
        Ok(Self::Results {
            sources: ResultSources(sources),
            target: target.into(),
        })
    }

    #[must_use]
    pub fn sources(&self) -> Vec<NodeIdentity> {
        match self {
            Self::Completion { source, .. } | Self::ConditionalCompletion { source, .. } => {
                vec![*source]
            }
            Self::Ports { source, .. } | Self::ConditionalPorts { source, .. } => {
                vec![source.owner()]
            }
            Self::Results { sources, .. } => sources
                .iter()
                .map(|source| match source {
                    OutputEndpoint::Node(id) => *id,
                    OutputEndpoint::Port(port) => port.owner(),
                })
                .collect(),
        }
    }
    #[must_use]
    pub const fn target(&self) -> NodeIdentity {
        match self {
            Self::Completion { target, .. } | Self::ConditionalCompletion { target, .. } => *target,
            Self::Ports { target, .. } | Self::ConditionalPorts { target, .. } => target.owner(),
            Self::Results { target, .. } => target.owner(),
        }
    }
}
