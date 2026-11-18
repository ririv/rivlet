//! Tagged declarations at the Node-API boundary; callbacks and values remain in JS.

use rivlet_core::{
    ConditionId, DeclarationNode, DeclarationPrerequisite, DeclaredGraph, DeclaredNodeKind,
    GraphDeclaration, GraphId, NodeActivation, NodeId, PortConnection, RuntimeCondition,
};
use serde::Deserialize;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Declaration {
    pub graphs: Vec<Scope>,
    pub nodes: Vec<Node>,
    pub conditions: Vec<Vec<usize>>,
    pub ports: Vec<Port>,
}

#[derive(Deserialize)]
#[serde(tag = "kind", rename_all = "camelCase", deny_unknown_fields)]
pub(crate) enum Scope {
    Root,
    Owned { node: usize },
}

#[derive(Deserialize)]
#[serde(
    tag = "kind",
    rename_all = "camelCase",
    rename_all_fields = "camelCase",
    deny_unknown_fields
)]
pub(crate) enum Node {
    Step {
        graph: usize,
        activation: Activation,
    },
    Graph {
        graph: usize,
        child_graph: usize,
        activation: Activation,
    },
}

#[derive(Deserialize)]
#[serde(tag = "kind", rename_all = "camelCase", deny_unknown_fields)]
pub(crate) enum Activation {
    Active {
        prerequisites: Vec<Prerequisite>,
        condition: Option<usize>,
    },
    Inactive,
}

#[derive(Deserialize)]
#[serde(tag = "kind", rename_all = "camelCase", deny_unknown_fields)]
pub(crate) enum Prerequisite {
    Required { node: usize },
    Static { node: usize, enabled: bool },
    Conditional { node: usize, condition: usize },
}

#[derive(Deserialize)]
#[serde(tag = "kind", rename_all = "camelCase", deny_unknown_fields)]
pub(crate) enum Port {
    Required {
        source: usize,
        target: usize,
    },
    Conditional {
        source: usize,
        target: usize,
        condition: usize,
    },
}

impl From<Declaration> for GraphDeclaration {
    fn from(declaration: Declaration) -> Self {
        let graphs = declaration.graphs.into_iter().map(|scope| match scope {
            Scope::Root => DeclaredGraph::Root,
            Scope::Owned { node } => DeclaredGraph::OwnedBy(NodeId::new(node)),
        });
        let nodes = declaration.nodes.into_iter().map(|node| {
            let (graph, kind, activation) = match node {
                Node::Step { graph, activation } => (graph, DeclaredNodeKind::Step, activation),
                Node::Graph {
                    graph,
                    child_graph,
                    activation,
                } => (
                    graph,
                    DeclaredNodeKind::Composite {
                        child_graph: GraphId::new(child_graph),
                    },
                    activation,
                ),
            };
            let activation = match activation {
                Activation::Inactive => NodeActivation::Inactive,
                Activation::Active {
                    prerequisites,
                    condition,
                } => NodeActivation::Active {
                    prerequisites: prerequisites
                        .into_iter()
                        .map(|source| match source {
                            Prerequisite::Required { node } => {
                                DeclarationPrerequisite::Required(NodeId::new(node))
                            }
                            Prerequisite::Static { node, enabled } => {
                                DeclarationPrerequisite::StaticConditional {
                                    node: NodeId::new(node),
                                    enabled,
                                }
                            }
                            Prerequisite::Conditional { node, condition } => {
                                DeclarationPrerequisite::RuntimeConditional {
                                    node: NodeId::new(node),
                                    condition: ConditionId::new(condition),
                                }
                            }
                        })
                        .collect(),
                    condition: condition.map(ConditionId::new),
                },
            };
            DeclarationNode::new(GraphId::new(graph), kind, activation)
        });
        let conditions = declaration
            .conditions
            .into_iter()
            .map(|reads| RuntimeCondition::new(reads.into_iter().map(NodeId::new)));
        let ports = declaration.ports.into_iter().map(|port| match port {
            Port::Required { source, target } => {
                PortConnection::new(NodeId::new(source), NodeId::new(target))
            }
            Port::Conditional {
                source,
                target,
                condition,
            } => PortConnection::conditional(
                NodeId::new(source),
                NodeId::new(target),
                ConditionId::new(condition),
            ),
        });
        Self::new(graphs, nodes, conditions, ports)
    }
}
