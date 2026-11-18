use super::{Edge, Graph, GraphError, InputEndpoint, Node, OutputEndpoint};
use rivlet_core::{Arc as TopologyArc, DagTopology, DirectedTopology, NodeId, NodeIdentity};
use std::collections::HashMap;

pub(crate) fn validate_graph(graph: &Graph) -> Result<(), GraphError> {
    // 本层校验 O(V+E+R+P)，R 为动作与条件的结果来源总数；普通边不分配来源 Vec。
    // 端口不近似成整个子图的阻塞关系。
    let mut indexes = HashMap::new();
    for (index, node) in graph.nodes().iter().enumerate() {
        if indexes.insert(node.id(), NodeId::new(index)).is_some() {
            return Err(GraphError::DuplicateNode(node.id()));
        }
    }
    let require = |id: NodeIdentity| indexes.get(&id).copied().ok_or(GraphError::UnknownNode(id));
    let mut arcs = Vec::new();
    for node in graph.nodes() {
        if let Some(condition) = node.condition() {
            for input in condition.inputs() {
                match input {
                    OutputEndpoint::Node(source) => {
                        arcs.push(TopologyArc::new(require(*source)?, require(node.id())?));
                    }
                    OutputEndpoint::Port(port) => {
                        require(port.owner())?;
                    }
                }
            }
        }
    }
    for edge in graph.edges() {
        let target = require(edge.target())?;
        match edge {
            Edge::Completion { source, .. } | Edge::ConditionalCompletion { source, .. } => {
                let source = require(*source)?;
                arcs.push(TopologyArc::new(source, target));
            }
            Edge::Ports { source, .. } | Edge::ConditionalPorts { source, .. } => {
                require(source.owner())?;
            }
            Edge::Results { sources, .. } => {
                for source in sources.iter() {
                    require(match source {
                        OutputEndpoint::Node(id) => *id,
                        OutputEndpoint::Port(port) => port.owner(),
                    })?;
                }
            }
        }
        if let Some(condition) = edge.condition() {
            for input in condition.inputs() {
                match input {
                    OutputEndpoint::Node(source) => {
                        let source = require(*source)?;
                        if matches!(edge, Edge::ConditionalCompletion { .. }) {
                            arcs.push(TopologyArc::new(source, target));
                        }
                    }
                    OutputEndpoint::Port(port) => {
                        require(port.owner())?;
                    }
                }
            }
        }
    }
    for targets in graph.declared_interface().inputs().values() {
        for endpoint in targets {
            require(match endpoint {
                InputEndpoint::Node(id) => *id,
                InputEndpoint::Port(port) => port.owner(),
            })?;
        }
    }
    for endpoint in graph.declared_interface().outputs().values() {
        require(match endpoint {
            OutputEndpoint::Node(id) => *id,
            OutputEndpoint::Port(port) => port.owner(),
        })?;
    }
    DagTopology::new(
        DirectedTopology::new(graph.nodes().len(), arcs).map_err(GraphError::Topology)?,
    )
    .map_err(GraphError::Topology)?;
    Ok(())
}
