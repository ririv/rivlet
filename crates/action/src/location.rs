//! Cohesive identity and mounted position for a graph-bound action context.
use rivlet::NodeIdentity;
use std::sync::Arc;

/// Immutable facts supplied by the execution host, not a reusable action identity.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NodeLocation {
    node_id: NodeIdentity,
    node_path: Arc<[usize]>,
    label_path: Arc<[String]>,
}
impl NodeLocation {
    #[must_use]
    pub fn new(node_id: NodeIdentity, node_path: Vec<usize>, label_path: Vec<String>) -> Self {
        Self {
            node_id,
            node_path: node_path.into(),
            label_path: label_path.into(),
        }
    }
    #[must_use]
    pub const fn node_id(&self) -> NodeIdentity {
        self.node_id
    }
    #[must_use]
    pub fn node_path(&self) -> &[usize] {
        &self.node_path
    }
    #[must_use]
    pub fn label_path(&self) -> &[String] {
        &self.label_path
    }
}
