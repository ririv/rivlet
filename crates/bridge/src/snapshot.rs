use std::collections::HashMap;

use action::ActionError;
use graph::{Edge, Node, OutputEndpoint, ProjectedKind, Projection, ResultTarget};
use serde::{Deserialize, Serialize};

use crate::types::{Type, describe};

mod validation;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Snapshot {
    pub nodes: Vec<NodeInfo>,
    pub scopes: Vec<ScopeInfo>,
    pub conditions: Vec<ConditionInfo>,
    pub ports: Vec<PortInfo>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct NodeInfo {
    pub identity: u64,
    pub label: String,
    pub graph: usize,
    pub path: Vec<usize>,
    pub kind: NodeKind,
    pub prerequisites: Vec<PrerequisiteInfo>,
    pub inputs: Vec<usize>,
    pub condition: Option<usize>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(tag = "kind", deny_unknown_fields)]
pub enum NodeKind {
    Step { inputs: Vec<Type>, output: Type },
    Graph { scope: usize },
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(tag = "kind", deny_unknown_fields)]
pub enum PrerequisiteInfo {
    Required { node: usize },
    Conditional { node: usize, condition: usize },
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ScopeInfo {
    pub owner: Option<usize>,
    pub nodes: Vec<usize>,
    pub inputs: Vec<(Option<String>, Vec<usize>)>,
    pub outputs: Vec<(Option<String>, usize)>,
    pub edges: Vec<EdgeInfo>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(tag = "kind", deny_unknown_fields)]
pub enum EdgeInfo {
    Order {
        source: usize,
        target: usize,
        condition: Option<usize>,
    },
    Ports {
        source: usize,
        target: usize,
        output: Option<String>,
        input: Option<String>,
        condition: Option<usize>,
    },
    Result {
        source: usize,
        target: usize,
        argument: usize,
        output: Option<Port>,
        input: Option<Port>,
    },
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Port {
    pub name: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PortInfo {
    pub source: usize,
    pub target: usize,
    pub condition: Option<usize>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(tag = "kind", deny_unknown_fields)]
pub enum Site {
    Node { target: usize },
    Edge { target: usize, index: usize },
}

impl Site {
    #[must_use]
    pub const fn target(&self) -> usize {
        match *self {
            Self::Node { target } | Self::Edge { target, .. } => target,
        }
    }
    #[must_use]
    pub const fn edge_index(&self) -> Option<usize> {
        match *self {
            Self::Node { .. } => None,
            Self::Edge { index, .. } => Some(index),
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ConditionInfo {
    pub inputs: Vec<usize>,
    pub types: Vec<Type>,
    pub site: Site,
}

impl Snapshot {
    /// Captures original hierarchy and lowered references without running user code.
    ///
    /// # Errors
    /// Rejects a type which cannot be represented by this transport.
    pub fn from_projection(projection: &Projection) -> Result<Self, ActionError> {
        let nodes = projection_nodes(projection)?;
        let edge_conditions: HashMap<_, _> = projection
            .conditions()
            .iter()
            .enumerate()
            .filter_map(|(condition, declaration)| {
                declaration
                    .edge_index()
                    .map(|edge| ((declaration.target().index(), edge), condition))
            })
            .collect();
        let mut edge_offsets = Vec::new();
        let scopes = projection
            .scopes()
            .iter()
            .map(|scope| {
                let (edges, offsets) = scope_edges(scope, &edge_conditions);
                edge_offsets.push(offsets);
                ScopeInfo {
                    owner: scope.owner().map(rivlet::NodeId::index),
                    nodes: scope.nodes().iter().map(|node| node.index()).collect(),
                    inputs: scope
                        .inputs()
                        .iter()
                        .map(|(name, nodes)| {
                            (
                                name.as_option().map(str::to_owned),
                                nodes.iter().map(|node| node.index()).collect(),
                            )
                        })
                        .collect(),
                    outputs: scope
                        .outputs()
                        .iter()
                        .map(|(name, node)| (name.as_option().map(str::to_owned), node.index()))
                        .collect(),
                    edges,
                }
            })
            .collect();
        let conditions = projection_conditions(projection, &nodes, &edge_offsets)?;
        let ports = projection
            .port_connections()
            .iter()
            .map(|port| PortInfo {
                source: port.source().index(),
                target: port.target().index(),
                condition: port
                    .prerequisite()
                    .condition()
                    .map(rivlet::ConditionId::index),
            })
            .collect();
        Ok(Self {
            nodes,
            scopes,
            conditions,
            ports,
        })
    }
}

fn projection_nodes(projection: &Projection) -> Result<Vec<NodeInfo>, ActionError> {
    projection
        .nodes()
        .iter()
        .map(|node| {
            Ok(NodeInfo {
                identity: node.identity().value(),
                label: node.label().to_owned(),
                graph: node.graph().index(),
                path: node.path().to_vec(),
                kind: match node.kind() {
                    ProjectedKind::Step(action) => NodeKind::Step {
                        inputs: action
                            .input_types()
                            .iter()
                            .map(|kind| describe(*kind))
                            .collect::<Result<_, _>>()?,
                        output: describe(action.output_type())?,
                    },
                    ProjectedKind::Graph(scope) => NodeKind::Graph {
                        scope: scope.index(),
                    },
                },
                prerequisites: node
                    .prerequisites()
                    .iter()
                    .map(|prerequisite| match *prerequisite {
                        rivlet::Prerequisite::Required(node) => {
                            PrerequisiteInfo::Required { node: node.index() }
                        }
                        rivlet::Prerequisite::Conditional { node, condition } => {
                            PrerequisiteInfo::Conditional {
                                node: node.index(),
                                condition: condition.index(),
                            }
                        }
                    })
                    .collect(),
                inputs: node.inputs().iter().map(|node| node.index()).collect(),
                condition: node.condition().map(rivlet::ConditionId::index),
            })
        })
        .collect::<Result<Vec<_>, ActionError>>()
}

fn projection_conditions(
    projection: &Projection,
    nodes: &[NodeInfo],
    edge_offsets: &[Vec<usize>],
) -> Result<Vec<ConditionInfo>, ActionError> {
    projection
        .conditions()
        .iter()
        .map(|condition| {
            let target = condition.target().index();
            Ok(ConditionInfo {
                inputs: condition.inputs().iter().map(|node| node.index()).collect(),
                types: condition
                    .input_types()
                    .iter()
                    .map(|kind| describe(*kind))
                    .collect::<Result<_, _>>()?,
                site: condition
                    .edge_index()
                    .map_or(Site::Node { target }, |index| Site::Edge {
                        target,
                        index: edge_offsets[nodes[target].graph][index],
                    }),
            })
        })
        .collect::<Result<_, ActionError>>()
}

fn scope_edges(
    scope: &graph::ProjectedScope,
    edge_conditions: &HashMap<(usize, usize), usize>,
) -> (Vec<EdgeInfo>, Vec<usize>) {
    let indexes: HashMap<_, _> = scope
        .structure()
        .nodes()
        .iter()
        .enumerate()
        .map(|(index, node)| (node.id(), index))
        .collect();
    let mut edges = Vec::new();
    let mut offsets = Vec::new();
    for (index, edge) in scope.structure().edges().iter().enumerate() {
        offsets.push(edges.len());
        let source = |id| indexes[&id];
        let target = indexes[&edge.target()];
        let global_target = scope.nodes()[target].index();
        let condition = edge_conditions.get(&(global_target, index)).copied();
        match edge {
            Edge::Completion { source: node, .. }
            | Edge::ConditionalCompletion { source: node, .. } => {
                edges.push(EdgeInfo::Order {
                    source: source(*node),
                    target,
                    condition,
                });
            }
            Edge::Ports {
                source: output,
                target: input,
            }
            | Edge::ConditionalPorts {
                source: output,
                target: input,
                ..
            } => edges.push(EdgeInfo::Ports {
                source: source(output.owner()),
                target,
                output: output.name().as_option().map(str::to_owned),
                input: input.name().as_option().map(str::to_owned),
                condition,
            }),
            Edge::Results {
                sources,
                target: receiver,
            } => {
                let input = match receiver {
                    ResultTarget::Step(_) => None,
                    ResultTarget::Port(port) => Some(Port {
                        name: port.name().as_option().map(str::to_owned),
                    }),
                };
                for (argument, endpoint) in sources.iter().enumerate() {
                    let (node, output) = match endpoint {
                        OutputEndpoint::Node(node) => (*node, None),
                        OutputEndpoint::Port(port) => (
                            port.owner(),
                            Some(Port {
                                name: port.name().as_option().map(str::to_owned),
                            }),
                        ),
                    };
                    edges.push(EdgeInfo::Result {
                        source: source(node),
                        target,
                        argument,
                        output,
                        input: input.clone(),
                    });
                }
            }
        }
    }

    (edges, offsets)
}
