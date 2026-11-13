use std::sync::Arc;

use action::NativeActionHandle;
use rivlet::NodeIdentity;

use crate::{GraphError, GraphInterfaceView, GraphStructure, Projection};

#[derive(Clone, Debug)]
struct Metadata {
    id: NodeIdentity,
    label: Arc<str>,
}

impl Metadata {
    fn new(label: impl Into<String>) -> Result<Self, GraphError> {
        let label = label.into();
        if label.is_empty() {
            return Err(GraphError::EmptyNodeLabel);
        }
        Ok(Self {
            id: NodeIdentity::allocate(),
            label: label.into(),
        })
    }
}

/// Common node references; concrete contents remain a closed [`NodeKind`] union.
pub trait Node {
    fn id(&self) -> NodeIdentity;
    fn label(&self) -> &str;
}

/// One Rust action with declaration-local metadata.
#[derive(Clone, Debug)]
pub struct Leaf {
    metadata: Metadata,
    action: NativeActionHandle,
}

impl Leaf {
    /// Creates a leaf. Cloning it preserves its declaration identity.
    ///
    /// # Errors
    /// Returns an error for an empty label.
    pub fn new(label: impl Into<String>, action: NativeActionHandle) -> Result<Self, GraphError> {
        Ok(Self {
            metadata: Metadata::new(label)?,
            action,
        })
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
    pub const fn action(&self) -> &NativeActionHandle {
        &self.action
    }
}

impl Node for Leaf {
    fn id(&self) -> NodeIdentity {
        self.id()
    }
    fn label(&self) -> &str {
        self.label()
    }
}

/// One graph node composing metadata and reusable structure, without a fake action.
#[derive(Clone, Debug)]
pub struct Graph {
    metadata: Metadata,
    structure: Arc<GraphStructure>,
}

impl Graph {
    /// Creates a distinct mount declaration for shared structure.
    ///
    /// # Errors
    /// Returns an error for an empty label.
    pub fn new(
        label: impl Into<String>,
        structure: Arc<GraphStructure>,
    ) -> Result<Self, GraphError> {
        Ok(Self {
            metadata: Metadata::new(label)?,
            structure,
        })
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
    pub const fn structure(&self) -> &Arc<GraphStructure> {
        &self.structure
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
}

/// The supported concrete node kinds, not an implementation-inheritance hierarchy.
#[derive(Clone, Debug)]
pub enum NodeKind {
    Leaf(Leaf),
    Graph(Graph),
}

impl Node for NodeKind {
    fn id(&self) -> NodeIdentity {
        match self {
            Self::Leaf(value) => value.id(),
            Self::Graph(value) => value.id(),
        }
    }
    fn label(&self) -> &str {
        match self {
            Self::Leaf(value) => value.label(),
            Self::Graph(value) => value.label(),
        }
    }
}

impl From<Leaf> for NodeKind {
    fn from(value: Leaf) -> Self {
        Self::Leaf(value)
    }
}
impl From<Graph> for NodeKind {
    fn from(value: Graph) -> Self {
        Self::Graph(value)
    }
}
