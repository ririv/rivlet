use std::sync::Arc;

use action::ActionHandle;
use rivlet::NodeIdentity;

use crate::{Condition, Edge, GraphError, GraphInterface, GraphInterfaceView, Projection};

#[derive(Clone, Debug)]
struct Metadata {
    id: NodeIdentity,
    label: Arc<str>,
    condition: Option<Condition>,
}

impl Metadata {
    fn new(label: impl Into<String>, condition: Option<Condition>) -> Result<Self, GraphError> {
        let label = label.into();
        if label.is_empty() {
            return Err(GraphError::EmptyNodeLabel);
        }
        Ok(Self {
            id: NodeIdentity::allocate(),
            label: label.into(),
            condition,
        })
    }
}

/// Common node references; concrete contents remain a closed [`NodeKind`] union.
pub trait Node {
    fn id(&self) -> NodeIdentity;
    fn label(&self) -> &str;
    fn condition(&self) -> Option<&Condition>;
}

/// One Rust action with declaration-local metadata.
#[derive(Clone, Debug)]
pub struct Step {
    metadata: Metadata,
    action: ActionHandle,
}

impl Step {
    /// Creates a step. Cloning it preserves its declaration identity.
    ///
    /// # Errors
    /// Returns an error for an empty label.
    pub fn new(label: impl Into<String>, action: ActionHandle) -> Result<Self, GraphError> {
        Ok(Self {
            metadata: Metadata::new(label, None)?,
            action,
        })
    }

    /// Creates a distinct step with a complete runtime condition.
    ///
    /// # Errors
    /// Returns an error for an empty label.
    pub fn with_condition(
        label: impl Into<String>,
        action: ActionHandle,
        condition: Condition,
    ) -> Result<Self, GraphError> {
        Ok(Self {
            metadata: Metadata::new(label, Some(condition))?,
            action,
        })
    }

    #[must_use]
    pub const fn condition(&self) -> Option<&Condition> {
        self.metadata.condition.as_ref()
    }

    #[must_use]
    pub fn id(&self) -> NodeIdentity {
        self.metadata.id
    }

    #[must_use]
    pub fn label(&self) -> &str {
        &self.metadata.label
    }

    #[must_use]
    pub const fn action(&self) -> &ActionHandle {
        &self.action
    }
}

impl Node for Step {
    fn id(&self) -> NodeIdentity {
        self.id()
    }
    fn label(&self) -> &str {
        self.label()
    }
    fn condition(&self) -> Option<&Condition> {
        self.condition()
    }
}

/// 完整的不可变图声明；克隆保留身份，新建 Graph 则分配独立身份。
#[derive(Clone, Debug)]
pub struct Graph {
    metadata: Metadata,
    nodes: Arc<[NodeKind]>,
    edges: Arc<[Edge]>,
    interface: GraphInterface,
}

impl Graph {
    /// Creates a graph with an explicitly empty interface.
    ///
    /// # Errors
    /// Returns an error for an invalid label, membership or topology.
    pub fn new(
        label: impl Into<String>,
        nodes: impl IntoIterator<Item = NodeKind>,
        edges: impl IntoIterator<Item = Edge>,
    ) -> Result<Self, GraphError> {
        Self::declare(label, nodes, edges, GraphInterface::default(), None)
    }

    /// Creates a complete graph with its public interface.
    ///
    /// # Errors
    /// Returns an error for an invalid declaration.
    pub fn with_interface(
        label: impl Into<String>,
        nodes: impl IntoIterator<Item = NodeKind>,
        edges: impl IntoIterator<Item = Edge>,
        interface: GraphInterface,
    ) -> Result<Self, GraphError> {
        Self::declare(label, nodes, edges, interface, None)
    }

    /// Creates a graph with its public interface and runtime condition.
    ///
    /// # Errors
    /// Returns an error for an invalid declaration.
    pub fn with_condition(
        label: impl Into<String>,
        nodes: impl IntoIterator<Item = NodeKind>,
        edges: impl IntoIterator<Item = Edge>,
        interface: GraphInterface,
        condition: Condition,
    ) -> Result<Self, GraphError> {
        Self::declare(label, nodes, edges, interface, Some(condition))
    }

    fn declare(
        label: impl Into<String>,
        nodes: impl IntoIterator<Item = NodeKind>,
        edges: impl IntoIterator<Item = Edge>,
        interface: GraphInterface,
        condition: Option<Condition>,
    ) -> Result<Self, GraphError> {
        let value = Self {
            metadata: Metadata::new(label, condition)?,
            nodes: nodes.into_iter().collect(),
            edges: edges.into_iter().collect(),
            interface,
        };
        crate::validation::validate_graph(&value)?;
        Ok(value)
    }

    #[must_use]
    pub const fn condition(&self) -> Option<&Condition> {
        self.metadata.condition.as_ref()
    }
    #[must_use]
    pub fn id(&self) -> NodeIdentity {
        self.metadata.id
    }
    #[must_use]
    pub fn label(&self) -> &str {
        &self.metadata.label
    }
    #[must_use]
    pub fn nodes(&self) -> &[NodeKind] {
        &self.nodes
    }
    #[must_use]
    pub fn edges(&self) -> &[Edge] {
        &self.edges
    }
    #[must_use]
    pub(crate) const fn declared_interface(&self) -> &GraphInterface {
        &self.interface
    }
    #[must_use]
    pub fn interface(&self) -> GraphInterfaceView<'_> {
        GraphInterfaceView::new(self)
    }

    /// Compiles this actual root node with the common compiler.
    ///
    /// # Errors
    /// Returns a structural or declaration compilation error.
    pub fn compile(&self) -> Result<crate::Compilation, GraphError> {
        Projection::from_node(self.clone().into())?.compile()
    }
}

impl Node for Graph {
    fn id(&self) -> NodeIdentity {
        self.id()
    }
    fn label(&self) -> &str {
        self.label()
    }
    fn condition(&self) -> Option<&Condition> {
        self.condition()
    }
}

/// The supported concrete node kinds, not an implementation-inheritance hierarchy.
#[derive(Clone, Debug)]
pub enum NodeKind {
    Step(Step),
    Graph(Graph),
}

impl Node for NodeKind {
    fn id(&self) -> NodeIdentity {
        match self {
            Self::Step(value) => value.id(),
            Self::Graph(value) => value.id(),
        }
    }
    fn label(&self) -> &str {
        match self {
            Self::Step(value) => value.label(),
            Self::Graph(value) => value.label(),
        }
    }
    fn condition(&self) -> Option<&Condition> {
        match self {
            Self::Step(value) => value.condition(),
            Self::Graph(value) => value.condition(),
        }
    }
}

impl From<Step> for NodeKind {
    fn from(value: Step) -> Self {
        Self::Step(value)
    }
}
impl From<Graph> for NodeKind {
    fn from(value: Graph) -> Self {
        Self::Graph(value)
    }
}
