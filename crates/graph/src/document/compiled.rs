use super::declaration::{Index, path_id, scope_id};
use crate::{Compilation, GraphError};
use inspection as doc;
use std::collections::{HashMap, HashSet};

pub(super) fn export(compilation: &Compilation) -> Result<doc::GraphDocument, GraphError> {
    let projection = compilation.projection();
    let root = projection.scopes()[0].declaration();
    let mut index = Index::new(root)?;
    let active = (0..compilation.graph().nodes().len())
        .map(|i| {
            let id = rivlet::NodeId::new(i);
            (path_id(compilation.node(id).path()), id)
        })
        .collect::<HashMap<_, _>>();
    let excluded_nodes = index
        .document
        .nodes
        .iter()
        .filter(|node| !active.contains_key(node.id()))
        .map(|node| node.id().to_owned())
        .collect();
    index
        .document
        .nodes
        .retain(|node| active.contains_key(node.id()));
    for node in &mut index.document.nodes {
        node.set_condition(
            compilation
                .node(active[node.id()])
                .condition()
                .map(|id| format!("condition:{}", id.index())),
        );
    }
    let mut conditions = vec![];
    for i in 0..compilation.graph().conditions().len() {
        let id = rivlet::ConditionId::new(i);
        let condition = compilation.condition(id);
        let name = format!("condition:{i}");
        conditions.push(doc::CompiledCondition {
            id: name,
            index: i,
            inputs: condition
                .inputs()
                .iter()
                .map(|id| path_id(compilation.node(*id).path()))
                .collect(),
        });
    }
    let (edges, excluded_edges) = export_edges(&index, compilation, &active);
    index.document.graphs.retain(|scope| {
        scope.id == index.document.root
            || scope
                .owner
                .as_ref()
                .is_some_and(|id| active.contains_key(id))
    });
    for scope in &mut index.document.graphs {
        scope.nodes.retain(|node| active.contains_key(node));
        scope.ports.retain_mut(|port| match port {
            doc::Port::Input { nodes, .. } => {
                nodes.retain(|node| active.contains_key(node));
                !nodes.is_empty()
            }
            doc::Port::Output { node, .. } => active.contains_key(node),
        });
    }
    let dependencies = dependencies(compilation);
    let document = doc::GraphDocument::Compiled(doc::CompilationDocument {
        schema_version: doc::SchemaVersion,
        root: index.document.root,
        parent: index.document.parent,
        graphs: index.document.graphs,
        nodes: index.document.nodes,
        edges,
        conditions,
        dependencies,
        excluded_nodes,
        excluded_edges,
    });
    document.validate()?;
    Ok(document)
}

fn export_edges(
    index: &Index,
    compilation: &Compilation,
    active: &HashMap<String, rivlet::NodeId>,
) -> (Vec<doc::Edge>, Vec<String>) {
    let projection = compilation.projection();
    let edge_conditions = (0..compilation.graph().conditions().len())
        .filter_map(|i| {
            let condition = compilation.condition(rivlet::ConditionId::new(i));
            condition.edge_index().map(|edge| {
                (
                    (path_id(compilation.node(condition.target()).path()), edge),
                    format!("condition:{i}"),
                )
            })
        })
        .collect::<HashMap<_, _>>();
    let disabled = projection
        .scopes()
        .iter()
        .map(|scope| {
            let owner = scope
                .owner()
                .map(|id| path_id(projection.nodes()[id.index()].path()));
            (scope_id(owner.as_deref()), scope.excluded_edges())
        })
        .collect::<HashMap<_, _>>();
    let original = &index.document.edges;
    let mut edges = vec![];
    for (edge_index, scope, position) in index.edge_origins.iter().copied() {
        let mut edge = original[edge_index].clone();
        if !active.contains_key(edge.source()) || !active.contains_key(edge.target()) {
            continue;
        }
        let scope_id = scope_id(index.scopes[scope].owner.as_deref());
        if disabled
            .get(&scope_id)
            .is_some_and(|edges| edges.contains(&position))
        {
            continue;
        }
        let target = edge.target().to_owned();
        match &mut edge {
            doc::Edge::Conditional(edge) => {
                edge.condition
                    .clone_from(&edge_conditions[&(target, position)]);
            }
            doc::Edge::Port(edge) => {
                edge.consumer_nodes.retain(|node| active.contains_key(node));
                if edge.consumer_nodes.is_empty() {
                    continue;
                }
                if edge.condition.is_some() {
                    edge.condition = Some(edge_conditions[&(target, position)].clone());
                }
            }
            doc::Edge::Result(edge) => {
                if let doc::ResultTarget::Port { consumers, .. } = &mut edge.target {
                    consumers.retain(|node| active.contains_key(node));
                    if consumers.is_empty() {
                        continue;
                    }
                }
            }
            doc::Edge::Order(_) => (),
        }
        edges.push(edge);
    }
    let retained = edges.iter().map(doc::Edge::id).collect::<HashSet<_>>();
    let excluded_edges = original
        .iter()
        .filter(|edge| !retained.contains(edge.id()))
        .map(|edge| edge.id().to_owned())
        .collect();
    (edges, excluded_edges)
}
fn dependencies(compilation: &Compilation) -> Vec<doc::Dependency> {
    let mut dependencies = vec![];
    for (i, node) in compilation.graph().nodes().iter().enumerate() {
        let target = path_id(compilation.node(rivlet::NodeId::new(i)).path());
        for prerequisite in node.prerequisites() {
            let source = path_id(compilation.node(prerequisite.node()).path());
            dependencies.push(match prerequisite.condition() {
                None => doc::Dependency::Required {
                    source,
                    target: target.clone(),
                },
                Some(condition) => doc::Dependency::Conditional {
                    source,
                    target: target.clone(),
                    condition: format!("condition:{}", condition.index()),
                },
            });
        }
    }
    dependencies
}
