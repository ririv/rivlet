use crate::{
    Condition, Edge, Graph, GraphError, InputEndpoint, Node, NodeKind, OutputEndpoint,
    StaticCondition, StaticRule,
};
use inspection as doc;
use std::collections::HashMap;

pub(super) fn path_id(path: &[usize]) -> String {
    path.iter()
        .map(usize::to_string)
        .collect::<Vec<_>>()
        .join(".")
}
pub(super) fn scope_id(owner: Option<&str>) -> String {
    owner.map_or_else(|| "graph:root".into(), |owner| format!("graph:{owner}"))
}
pub(super) struct Scope {
    pub graph: Graph,
    pub owner: Option<String>,
    pub locals: HashMap<rivlet::NodeIdentity, String>,
    pub children: HashMap<rivlet::NodeIdentity, usize>,
}
pub(super) struct Index {
    pub document: doc::DeclarationDocument,
    pub scopes: Vec<Scope>,
    pub edge_origins: Vec<(usize, usize, usize)>,
    conditions: Vec<(usize, String, Condition)>,
}
impl Index {
    pub fn new(root: &Graph) -> Result<Self, GraphError> {
        let mut value = Self {
            document: doc::DeclarationDocument {
                schema_version: doc::SchemaVersion,
                root: "graph:root".into(),
                parent: None,
                graphs: vec![],
                nodes: vec![],
                edges: vec![],
                conditions: vec![],
                static_rules: vec![],
                selections: vec![],
            },
            scopes: vec![],
            edge_origins: vec![],
            conditions: vec![],
        };
        value.visit(root, None, &[], &[])?;
        for scope in 0..value.scopes.len() {
            value.fill_scope(scope)?;
        }
        for (scope, id, condition) in &value.conditions {
            value.document.conditions.push(doc::Condition {
                id: id.clone(),
                inputs: condition
                    .inputs()
                    .iter()
                    .map(|input| value.output(*scope, input))
                    .collect::<Result<_, _>>()?,
            });
        }
        Ok(value)
    }
    fn visit(
        &mut self,
        graph: &Graph,
        owner: Option<String>,
        path: &[usize],
        labels: &[String],
    ) -> Result<usize, GraphError> {
        let index = self.scopes.len();
        let scope = scope_id(owner.as_deref());
        let ids = graph
            .nodes()
            .iter()
            .enumerate()
            .map(|(position, node)| {
                let mut path = path.to_vec();
                path.push(position);
                (node.id(), path_id(&path))
            })
            .collect::<HashMap<_, _>>();
        self.document.graphs.push(doc::Scope {
            id: scope.clone(),
            label: owner.as_ref().map_or_else(
                || {
                    graph
                        .nodes()
                        .first()
                        .map_or_else(|| "graph".into(), |node| node.label().to_owned())
                },
                |_| labels.last().expect("child label").clone(),
            ),
            label_path: labels.to_vec(),
            owner: owner.clone(),
            nodes: graph
                .nodes()
                .iter()
                .map(|node| ids[&node.id()].clone())
                .collect(),
            ports: vec![],
        });
        self.scopes.push(Scope {
            graph: graph.clone(),
            owner,
            locals: ids,
            children: HashMap::new(),
        });
        for (position, node) in graph.nodes().iter().enumerate() {
            let id = self.scopes[index].locals[&node.id()].clone();
            let mut child_labels = labels.to_vec();
            child_labels.push(node.label().to_owned());
            let condition = node
                .condition()
                .map(|condition| self.condition(index, &id, condition));
            match node {
                NodeKind::Step(_) => self.document.nodes.push(doc::Node::Leaf(doc::Step {
                    id,
                    node_id: node.id().value().to_string(),
                    graph: scope.clone(),
                    label: node.label().into(),
                    label_path: child_labels,
                    action_kind: doc::ActionKind::Rust,
                    condition,
                })),
                NodeKind::Graph(child) => {
                    self.document.nodes.push(doc::Node::Subgraph(doc::Subgraph {
                        id: id.clone(),
                        node_id: node.id().value().to_string(),
                        graph: scope.clone(),
                        label: node.label().into(),
                        label_path: child_labels.clone(),
                        child_graph: scope_id(Some(&id)),
                        condition,
                    }));
                    let mut child_path = path.to_vec();
                    child_path.push(position);
                    let mounted = self.visit(child, Some(id), &child_path, &child_labels)?;
                    self.scopes[index].children.insert(node.id(), mounted);
                }
            }
        }
        Ok(index)
    }
    fn condition(&mut self, scope: usize, target: &str, value: &Condition) -> String {
        let id = format!("{target}/condition:{}", self.conditions.len());
        self.conditions.push((scope, id.clone(), value.clone()));
        id
    }
    pub fn local(&self, scope: usize, node: rivlet::NodeIdentity) -> Result<String, GraphError> {
        self.scopes[scope]
            .locals
            .get(&node)
            .cloned()
            .ok_or(GraphError::UnknownNode(node))
    }
    fn child(&self, scope: usize, owner: rivlet::NodeIdentity) -> Result<usize, GraphError> {
        self.scopes[scope]
            .children
            .get(&owner)
            .copied()
            .ok_or(GraphError::ExpectedGraph(owner))
    }
    pub fn output(&self, scope: usize, endpoint: &OutputEndpoint) -> Result<String, GraphError> {
        match endpoint {
            OutputEndpoint::Node(node) => self.local(scope, *node),
            OutputEndpoint::Port(port) => {
                let child = self.child(scope, port.owner())?;
                let source = self.scopes[child]
                    .graph
                    .declared_interface()
                    .outputs()
                    .get(port.name())
                    .ok_or_else(|| GraphError::UnknownOutputPort(port.name().clone()))?;
                self.output(child, source)
            }
        }
    }
    pub fn input(&self, scope: usize, endpoint: &InputEndpoint) -> Result<Vec<String>, GraphError> {
        match endpoint {
            InputEndpoint::Node(node) => Ok(vec![self.local(scope, *node)?]),
            InputEndpoint::Port(port) => {
                let child = self.child(scope, port.owner())?;
                let targets = self.scopes[child]
                    .graph
                    .declared_interface()
                    .inputs()
                    .get(port.name())
                    .ok_or_else(|| GraphError::UnknownInputPort(port.name().clone()))?;
                let mut values = vec![];
                for target in targets {
                    values.extend(self.input(child, target)?);
                }
                Ok(values)
            }
        }
    }
    fn fill_scope(&mut self, scope: usize) -> Result<(), GraphError> {
        let graph = self.scopes[scope].graph.clone();
        let id = scope_id(self.scopes[scope].owner.as_deref());
        for (name, targets) in graph.declared_interface().inputs() {
            let mut nodes = vec![];
            for target in targets {
                nodes.extend(self.input(scope, target)?);
            }
            self.document.graphs[scope].ports.push(doc::Port::Input {
                port: name.as_option().map(str::to_owned),
                nodes,
            });
        }
        for (name, source) in graph.declared_interface().outputs() {
            let node = self.output(scope, source)?;
            self.document.graphs[scope].ports.push(doc::Port::Output {
                port: name.as_option().map(str::to_owned),
                node,
            });
        }
        for (position, edge) in graph.edges().iter().enumerate() {
            self.fill_edge(scope, &id, position, edge)?;
        }
        self.fill_static(scope, &id, &graph)?;
        Ok(())
    }
    fn fill_edge(
        &mut self,
        scope: usize,
        id: &str,
        position: usize,
        edge: &Edge,
    ) -> Result<(), GraphError> {
        let base = format!("{id}/edge:{position}");
        let source = edge
            .sources()
            .first()
            .copied()
            .map(|source| self.local(scope, source))
            .transpose()?;
        let target = self.local(scope, edge.target())?;
        let condition = edge
            .condition()
            .map(|condition| self.condition(scope, &target, condition));
        let values = match edge {
            Edge::Completion { .. } => vec![doc::Edge::Order(doc::OrderEdge {
                id: base,
                graph: id.to_owned(),
                source: source.expect("order source"),
                target,
            })],
            Edge::ConditionalCompletion { .. } => {
                vec![doc::Edge::Conditional(doc::ConditionalEdge {
                    id: base,
                    graph: id.to_owned(),
                    source: source.expect("conditional source"),
                    target,
                    condition: condition.expect("edge condition"),
                })]
            }
            Edge::Ports {
                source: port,
                target: input,
            }
            | Edge::ConditionalPorts {
                source: port,
                target: input,
                ..
            } => vec![doc::Edge::Port(doc::PortEdge {
                id: base,
                graph: id.to_owned(),
                source: self.local(scope, port.owner())?,
                target,
                source_port: port.name().as_option().map(str::to_owned),
                target_port: input.name().as_option().map(str::to_owned),
                condition,
                provider_node: self.output(scope, &OutputEndpoint::Port(port.clone()))?,
                consumer_nodes: self.input(scope, &InputEndpoint::Port(input.clone()))?,
            })],
            Edge::Results {
                sources,
                target: receiver,
            } => {
                let mut values = vec![];
                for (argument_index, source) in sources.iter().enumerate() {
                    let source = match source {
                        OutputEndpoint::Node(node) => {
                            doc::ResultSource::Direct(self.local(scope, *node)?)
                        }
                        OutputEndpoint::Port(port) => doc::ResultSource::Port {
                            owner: self.local(scope, port.owner())?,
                            name: port.name().as_option().map(str::to_owned),
                            provider: self.output(scope, source)?,
                        },
                    };
                    let target = match receiver {
                        crate::ResultTarget::Step(node) => {
                            doc::ResultTarget::Direct(self.local(scope, *node)?)
                        }
                        crate::ResultTarget::Port(port) => doc::ResultTarget::Port {
                            owner: self.local(scope, port.owner())?,
                            name: port.name().as_option().map(str::to_owned),
                            consumers: self.input(scope, &InputEndpoint::Port(port.clone()))?,
                        },
                    };
                    values.push(doc::Edge::Result(doc::ResultEdge {
                        id: format!("{base}/input:{argument_index}"),
                        graph: id.to_owned(),
                        source,
                        target,
                        argument_index,
                    }));
                }
                values
            }
        };
        for edge in values {
            self.edge_origins
                .push((self.document.edges.len(), scope, position));
            self.document.edges.push(edge);
        }
        Ok(())
    }
    fn fill_static(&mut self, scope: usize, id: &str, graph: &Graph) -> Result<(), GraphError> {
        for (position, rule) in graph.static_rules().iter().enumerate() {
            let condition = format!("{id}/static:{position}");
            match rule {
                StaticRule::When(case) => self.document.static_rules.push(doc::StaticRule::Node {
                    node: self.local(scope, case.node())?,
                    condition,
                }),
                StaticRule::Edge { source, target, .. } => {
                    self.document.static_rules.push(doc::StaticRule::Edge {
                        source: self.local(scope, *source)?,
                        target: self.local(scope, *target)?,
                        condition,
                    });
                }
                StaticRule::Select(cases) => self.document.selections.push(doc::Selection {
                    id: format!("{id}/selection:{position}"),
                    cases: cases
                        .iter()
                        .enumerate()
                        .map(|(i, case)| {
                            Ok(doc::Case {
                                node: self.local(scope, case.node())?,
                                condition: match case.condition() {
                                    StaticCondition::Known(value) => {
                                        doc::StaticValue::Known(*value)
                                    }
                                    StaticCondition::Evaluate(_) => {
                                        doc::StaticValue::Opaque(format!("{condition}/case:{i}"))
                                    }
                                },
                            })
                        })
                        .collect::<Result<_, GraphError>>()?,
                }),
            }
        }
        Ok(())
    }
}
