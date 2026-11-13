use std::collections::HashMap;
use std::sync::Arc;

use rivlet::{Arc as TopologyArc, DagTopology, DirectedTopology, NodeId, NodeIdentity};

use crate::{
    Edge, GraphError, GraphInterface, InputEndpoint, Node, NodeKind, OutputEndpoint, Projection,
};

/// Reusable immutable structure; metadata belongs to its actual Graph nodes.
#[derive(Clone, Debug)]
pub struct GraphStructure {
    nodes: Vec<NodeKind>,
    edges: Vec<Edge>,
    interface: GraphInterface,
}

impl GraphStructure {
    /// Creates a structure with an explicitly empty interface.
    ///
    /// # Errors
    /// Returns an error for invalid local membership or topology.
    pub fn new(
        nodes: impl IntoIterator<Item = NodeKind>,
        edges: impl IntoIterator<Item = Edge>,
    ) -> Result<Self, GraphError> {
        Self::with_interface(nodes, edges, GraphInterface::default())
    }

    /// Creates one complete structure, without subsequent interface overrides.
    ///
    /// # Errors
    /// Returns an error for unknown/repeated nodes, invalid ports or local cycles.
    pub fn with_interface(
        nodes: impl IntoIterator<Item = NodeKind>,
        edges: impl IntoIterator<Item = Edge>,
        interface: GraphInterface,
    ) -> Result<Self, GraphError> {
        let value = Self {
            nodes: nodes.into_iter().collect(),
            edges: edges.into_iter().collect(),
            interface,
        };
        value.validate()?;
        Ok(value)
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
    pub const fn interface(&self) -> &GraphInterface {
        &self.interface
    }

    /// Compiles a body without manufacturing an executable root action.
    ///
    /// # Errors
    /// Returns an error for a fully resolved dependency cycle.
    pub fn compile_structure(&self) -> Result<crate::Compilation, GraphError> {
        Projection::from_structure(Arc::new(self.clone()))?.compile()
    }

    fn validate(&self) -> Result<(), GraphError> {
        // 本层节点/边校验 O(V+E+P)，端口不近似成整个子图的阻塞关系。
        let mut indexes = HashMap::new();
        for (index, node) in self.nodes.iter().enumerate() {
            if indexes.insert(node.id(), NodeId::new(index)).is_some() {
                return Err(GraphError::DuplicateNode(node.id()));
            }
        }
        let require =
            |id: NodeIdentity| indexes.get(&id).copied().ok_or(GraphError::UnknownNode(id));
        let mut arcs = Vec::new();
        for edge in &self.edges {
            let source = require(edge.source())?;
            let target = require(edge.target())?;
            if matches!(edge, Edge::Completion { .. }) {
                arcs.push(TopologyArc::new(source, target));
            }
        }
        for targets in self.interface.inputs().values() {
            for endpoint in targets {
                require(match endpoint {
                    InputEndpoint::Node(id) => *id,
                    InputEndpoint::Port(port) => port.owner(),
                })?;
            }
        }
        for endpoint in self.interface.outputs().values() {
            require(match endpoint {
                OutputEndpoint::Node(id) => *id,
                OutputEndpoint::Port(port) => port.owner(),
            })?;
        }
        DagTopology::new(
            DirectedTopology::new(self.nodes.len(), arcs).map_err(GraphError::Topology)?,
        )
        .map_err(GraphError::Topology)?;
        Ok(())
    }
}
