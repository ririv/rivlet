//! Lazy mounted-reference lookup over the exact compiled projection.
use crate::{Compilation, Node, OutputEndpoint, PortName, ProjectedKind};
use rivlet::{NodeId, NodeIdentity};
use std::collections::HashMap;

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum LookupError {
    NodeUnavailable(NodeIdentity),
    AmbiguousNode(NodeIdentity),
    PortUnavailable { owner: NodeIdentity, name: PortName },
}
impl std::fmt::Display for LookupError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::NodeUnavailable(id) => {
                write!(f, "node {} is not active in this compilation", id.value())
            }
            Self::AmbiguousNode(id) => write!(
                f,
                "node {} has multiple mounts; select a uniquely mounted graph output",
                id.value()
            ),
            Self::PortUnavailable { owner, name } => write!(
                f,
                "output {name} of graph {} has no active provider",
                owner.value()
            ),
        }
    }
}
impl std::error::Error for LookupError {}

enum Mount {
    Unique(NodeId),
    Ambiguous,
}
pub(crate) struct LookupIndex {
    mounts: HashMap<NodeIdentity, Mount>,
    compiled: Vec<Option<NodeId>>,
}
impl LookupIndex {
    pub(crate) fn new(compilation: &Compilation) -> Self {
        let mut mounts = HashMap::new();
        let mut compiled = vec![None; compilation.projection().nodes().len()];
        for (index, origin) in compilation.mapping().iter().enumerate() {
            let id = NodeId::new(index);
            compiled[origin.index()] = Some(id);
            let identity = compilation.node(id).identity();
            mounts
                .entry(identity)
                .and_modify(|entry| *entry = Mount::Ambiguous)
                .or_insert(Mount::Unique(id));
        }
        Self { mounts, compiled }
    }
    fn unique(&self, identity: NodeIdentity) -> Result<NodeId, LookupError> {
        match self.mounts.get(&identity) {
            None => Err(LookupError::NodeUnavailable(identity)),
            Some(Mount::Ambiguous) => Err(LookupError::AmbiguousNode(identity)),
            Some(Mount::Unique(node)) => Ok(*node),
        }
    }
}
impl Compilation {
    fn lookup(&self) -> &LookupIndex {
        self.lookup_index().get_or_init(|| LookupIndex::new(self))
    }
    /// Resolves a unique actual mount, not an arbitrary first matching declaration.
    ///
    /// # Errors
    /// Rejects absent, pruned or multiply mounted declarations.
    pub fn node_index(&self, node: &(impl Node + ?Sized)) -> Result<NodeId, LookupError> {
        self.lookup().unique(node.id())
    }
    /// Resolves a result source through the owning graph's explicit output interface.
    ///
    /// # Errors
    /// Rejects ambiguous owners and inactive providers; never recompiles the graph.
    pub fn result_index(&self, source: impl Into<OutputEndpoint>) -> Result<NodeId, LookupError> {
        let index = self.lookup();
        match source.into() {
            OutputEndpoint::Node(identity) => index.unique(identity),
            OutputEndpoint::Port(port) => {
                let owner = index.unique(port.owner())?;
                let ProjectedKind::Graph(scope) = self.node(owner).kind() else {
                    unreachable!("an OutputPort can only be created by a Graph")
                };
                self.projection().scopes()[scope.index()]
                    .outputs()
                    .get(port.name())
                    .and_then(|origin| index.compiled[origin.index()])
                    .ok_or_else(|| LookupError::PortUnavailable {
                        owner: port.owner(),
                        name: port.name().clone(),
                    })
            }
        }
    }
}
