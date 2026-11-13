//! Rust-owned graph declarations whose leaf actions stay in Rust.

use std::error::Error;
use std::fmt::{self, Display, Formatter};

use action::NativeActionHandle;
use rivlet::{
    Arc, DeclarationNode, DeclarationPrerequisite, DeclaredGraph, DeclaredNodeKind,
    GraphCompilation, GraphDeclaration, GraphId, NodeActivation, NodeId, NodeIdentity,
};

/// One Rust-owned executable step in a [`NativeGraphStructure`].
#[derive(Clone, Debug)]
pub struct NativeLeaf {
    id: NodeIdentity,
    label: String,
    action: NativeActionHandle,
}

impl NativeLeaf {
    /// Creates a native step with an explicit display label.
    ///
    /// # Errors
    /// Returns an error when the label is empty.
    pub fn new(
        label: impl Into<String>,
        action: NativeActionHandle,
    ) -> Result<Self, NativeGraphError> {
        let label = label.into();
        if label.is_empty() {
            return Err(NativeGraphError::EmptyNodeLabel);
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
pub struct NativeGraphStructure {
    leaves: Vec<NativeLeaf>,
    arcs: Vec<Arc>,
}

impl NativeGraphStructure {
    /// Creates a native graph while preserving edges as graph-owned topology.
    ///
    /// # Errors
    /// Returns an error when an edge references an unknown step or the declared
    /// topology contains a cycle.
    pub fn new(
        leaves: impl IntoIterator<Item = NativeLeaf>,
        arcs: impl IntoIterator<Item = Arc>,
    ) -> Result<Self, NativeGraphError> {
        let graph = Self {
            leaves: leaves.into_iter().collect(),
            arcs: arcs.into_iter().collect(),
        };
        // 编译一次只用于验证完整声明。时间和额外空间均为 O(V + E)。
        graph.compile_structure()?;
        Ok(graph)
    }

    #[must_use]
    pub fn leaves(&self) -> &[NativeLeaf] {
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
    pub fn compile_structure(&self) -> Result<GraphCompilation, NativeGraphError> {
        let mut prerequisites = vec![Vec::new(); self.leaves.len()];
        for arc in &self.arcs {
            let Some(target) = prerequisites.get_mut(arc.target.index()) else {
                return Err(NativeGraphError::UnknownNode(arc.target));
            };
            if arc.source.index() >= self.leaves.len() {
                return Err(NativeGraphError::UnknownNode(arc.source));
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
        .map_err(NativeGraphError::Declaration)
    }
}

/// A native graph declaration failure detected before execution.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum NativeGraphError {
    EmptyNodeLabel,
    UnknownNode(NodeId),
    Declaration(rivlet::DeclarationError),
}

impl Display for NativeGraphError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        match self {
            Self::EmptyNodeLabel => formatter.write_str("native step label must not be empty"),
            Self::UnknownNode(step) => {
                write!(
                    formatter,
                    "native graph edge references unknown step: {}",
                    step.index()
                )
            }
            Self::Declaration(error) => Display::fmt(error, formatter),
        }
    }
}

impl Error for NativeGraphError {
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

    use super::{NativeGraphStructure, NativeLeaf};

    struct Noop;

    impl NativeAction for Noop {
        fn execute(&self, _context: &NativeActionContext) -> Result<(), NativeActionError> {
            Ok(())
        }
    }

    fn step(label: &str) -> NativeLeaf {
        NativeLeaf::new(label, NativeActionHandle::new(Noop)).unwrap()
    }

    #[test]
    fn native_graph_uses_the_common_compiled_execution_graph() {
        let graph = NativeGraphStructure::new(
            [step("prepare"), step("compile")],
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
