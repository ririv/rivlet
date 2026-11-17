//! Linear-time presentation indexes shared by both renderers.
use crate::{Dependency, Edge, GraphDocument, StaticRule, StaticValue};
use std::collections::HashMap;

pub(crate) struct Presentation<'a> {
    pub static_nodes: HashMap<&'a str, Vec<&'a str>>,
    pub static_edges: HashMap<(&'a str, &'a str), Vec<&'a str>>,
    pub selections: HashMap<&'a str, Vec<&'a StaticValue>>,
    pub condition_inputs: HashMap<&'a str, &'a [String]>,
    pub edges: HashMap<&'a str, Vec<&'a Edge>>,
    pub dependencies: HashMap<&'a str, Vec<&'a Dependency>>,
}
impl<'a> Presentation<'a> {
    pub fn new(document: &'a GraphDocument) -> Self {
        let mut value = Self {
            static_nodes: HashMap::new(),
            static_edges: HashMap::new(),
            selections: HashMap::new(),
            condition_inputs: document.condition_inputs().into_iter().collect(),
            edges: HashMap::new(),
            dependencies: HashMap::new(),
        };
        for edge in document.view().edges {
            value.edges.entry(edge.graph()).or_default().push(edge);
        }
        match document {
            GraphDocument::Declaration(declaration) => {
                for rule in &declaration.static_rules {
                    match rule {
                        StaticRule::Node { node, condition } => {
                            value.static_nodes.entry(node).or_default().push(condition);
                        }
                        StaticRule::Edge {
                            source,
                            target,
                            condition,
                        } => value
                            .static_edges
                            .entry((source, target))
                            .or_default()
                            .push(condition),
                    }
                }
                for selection in &declaration.selections {
                    for case in &selection.cases {
                        value
                            .selections
                            .entry(&case.node)
                            .or_default()
                            .push(&case.condition);
                    }
                }
            }
            GraphDocument::Compiled(compilation) => {
                for dep in &compilation.dependencies {
                    let (Dependency::Required { target, .. }
                    | Dependency::Conditional { target, .. }) = dep;
                    value.dependencies.entry(target).or_default().push(dep);
                }
            }
        }
        value
    }
}
