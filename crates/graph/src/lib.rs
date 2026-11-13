//! Rust graph declarations whose leaf actions stay in Rust.
//!
//! This declaration API currently exposes flat structures. Hierarchy and port
//! compilation remain available in core, not yet through this high-level API.
//!
//! ```
//! use action::NativeActionHandle;
//! use graph::{GraphError, GraphStructure, Leaf};
//! use rivlet::{Arc, NodeId};
//!
//! fn sequence(
//!     prepare: NativeActionHandle,
//!     compile: NativeActionHandle,
//! ) -> Result<GraphStructure, GraphError> {
//!     GraphStructure::new(
//!         [Leaf::new("prepare", prepare)?, Leaf::new("compile", compile)?],
//!         [Arc::new(NodeId::new(0), NodeId::new(1))],
//!     )
//! }
//! ```

use std::error::Error;
use std::fmt::{self, Display, Formatter};

use action::NativeActionHandle;
use rivlet::{
    Arc, DeclarationNode, DeclarationPrerequisite, DeclaredGraph, DeclaredNodeKind,
    GraphCompilation, GraphDeclaration, GraphId, NodeActivation, NodeId, NodeIdentity,
};

/// One Rust-owned executable leaf in a [`GraphStructure`].
#[derive(Clone, Debug)]
pub struct Leaf {
    id: NodeIdentity,
    label: String,
    action: NativeActionHandle,
}

impl Leaf {
    /// Creates a leaf with an explicit display label.
    ///
    /// # Errors
    /// Returns an error when the label is empty.
    pub fn new(label: impl Into<String>, action: NativeActionHandle) -> Result<Self, GraphError> {
        let label = label.into();
        if label.is_empty() {
            return Err(GraphError::EmptyNodeLabel);
        }
        Ok(Self {
            id: NodeIdentity::allocate(),
            label,
            action,
        })
    }

    #[must_use]
    pub const fn id(&self) -> NodeIdentity {
        self.id
    }

    #[must_use]
    pub fn label(&self) -> &str {
        &self.label
    }

    #[must_use]
    pub const fn action(&self) -> &NativeActionHandle {
        &self.action
    }
}

/// A complete Rust declaration graph with Rust-owned leaf actions.
#[derive(Clone, Debug)]
pub struct GraphStructure {
    leaves: Vec<Leaf>,
    arcs: Vec<Arc>,
}

impl GraphStructure {
    /// Creates a native graph while preserving edges as graph-owned topology.
    ///
    /// # Errors
    /// Returns an error when an edge references an unknown step or the declared
    /// topology contains a cycle.
    pub fn new(
        leaves: impl IntoIterator<Item = Leaf>,
        arcs: impl IntoIterator<Item = Arc>,
    ) -> Result<Self, GraphError> {
        let graph = Self {
            leaves: leaves.into_iter().collect(),
            arcs: arcs.into_iter().collect(),
        };
        // 编译一次只用于验证完整声明。时间和额外空间均为 O(V + E)。
        graph.compile_structure()?;
        Ok(graph)
    }

    #[must_use]
    pub fn leaves(&self) -> &[Leaf] {
        &self.leaves
    }

    #[must_use]
    pub fn arcs(&self) -> &[Arc] {
        &self.arcs
    }

    /// Compiles the native declaration with the shared graph compiler.
    ///
    /// # Errors
    /// Returns the common graph declaration error when the topology is invalid.
    pub fn compile_structure(&self) -> Result<GraphCompilation, GraphError> {
        let mut prerequisites = vec![Vec::new(); self.leaves.len()];
        for arc in &self.arcs {
            let Some(target) = prerequisites.get_mut(arc.target.index()) else {
                return Err(GraphError::UnknownNode(arc.target));
            };
            if arc.source.index() >= self.leaves.len() {
                return Err(GraphError::UnknownNode(arc.source));
            }
            target.push(DeclarationPrerequisite::Required(arc.source));
        }
        GraphDeclaration::new(
            [DeclaredGraph::Root],
            prerequisites.into_iter().map(|prerequisites| {
                DeclarationNode::new(
                    GraphId::new(0),
                    DeclaredNodeKind::Leaf,
                    NodeActivation::Active {
                        prerequisites,
                        condition: None,
                    },
                )
            }),
            [],
            [],
        )
        .compile()
        .map_err(GraphError::Declaration)
    }
}

/// A native graph declaration failure detected before execution.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum GraphError {
    EmptyNodeLabel,
    UnknownNode(NodeId),
    Declaration(rivlet::DeclarationError),
}

impl Display for GraphError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        match self {
            Self::EmptyNodeLabel => formatter.write_str("leaf label must not be empty"),
            Self::UnknownNode(step) => {
                write!(
                    formatter,
                    "graph edge references unknown node: {}",
                    step.index()
                )
            }
            Self::Declaration(error) => Display::fmt(error, formatter),
        }
    }
}

impl Error for GraphError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Declaration(error) => Some(error),
            _ => None,
        }
    }
}

#[cfg(test)]
mod tests {
    use action::{NativeAction, NativeActionContext, NativeActionError, NativeActionHandle};
    use rivlet::{Arc, NodeId};

    use super::{GraphStructure, Leaf};

    struct Noop;

    impl NativeAction for Noop {
        fn execute(&self, _context: &NativeActionContext) -> Result<(), NativeActionError> {
            Ok(())
        }
    }

    fn leaf(label: &str) -> Leaf {
        Leaf::new(label, NativeActionHandle::new(Noop)).unwrap()
    }

    #[test]
    fn native_graph_uses_the_common_compiled_execution_graph() {
        let graph = GraphStructure::new(
            [leaf("prepare"), leaf("compile")],
            [Arc::new(NodeId::new(0), NodeId::new(1))],
        )
        .unwrap();

        let compilation = graph.compile_structure().unwrap();
        assert_eq!(
            compilation
                .graph()
                .dependency_topology()
                .topological_order(),
            &[NodeId::new(0), NodeId::new(1)]
        );
    }
}
