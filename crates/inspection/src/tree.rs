use crate::presentation::Presentation;
use crate::validate::Index;
use crate::{
    ActionKind, Dependency, DocumentError, Edge, GraphDocument, Node, RenderOptions, ResultSource,
    ResultTarget, StaticValue,
};
use std::fmt::Write;

pub(crate) fn port_name(name: Option<&str>) -> &str {
    name.unwrap_or("(anonymous)")
}
fn append(target: &mut String, arguments: std::fmt::Arguments<'_>) {
    target
        .write_fmt(arguments)
        .expect("writing to String is infallible");
}
pub(crate) fn condition_label(id: &str, options: &RenderOptions) -> String {
    options
        .condition_labels
        .get(id)
        .cloned()
        .unwrap_or_else(|| format!("runtime:{id}"))
}
pub(crate) fn static_value(value: &StaticValue) -> String {
    match value {
        StaticValue::Known(true) => "True".into(),
        StaticValue::Known(false) => "False".into(),
        StaticValue::Opaque(id) => format!("opaque:{id}"),
    }
}
pub(crate) fn result_label(edge: &crate::ResultEdge) -> String {
    let mut label = format!("action input: {}", edge.argument_index);
    if let ResultSource::Port { name, .. } = &edge.source {
        append(
            &mut label,
            format_args!(" from {}", port_name(name.as_deref())),
        );
    }
    if let ResultTarget::Port { name, .. } = &edge.target {
        append(
            &mut label,
            format_args!(" to {}", port_name(name.as_deref())),
        );
    }
    label
}
/// Renders the hierarchy and dependency annotations of a validated document.
///
/// # Errors
/// Returns an error for an invalid document or dangling structural reference.
pub fn render_tree(
    document: &GraphDocument,
    options: &RenderOptions,
) -> Result<String, DocumentError> {
    let index = Index::new(document)?;
    let root = index.scopes[index.view.root];
    let title = options.root_label.as_deref().unwrap_or(&root.label);
    let presentation = Presentation::new(document);
    if let GraphDocument::Compiled(_) = document {
        return Ok(render_compiled(&index, &presentation, title, options));
    }
    let mut lines = vec![title.to_owned()];
    append_scope(
        &index,
        &presentation,
        index.view.root,
        "",
        options,
        &mut lines,
    )?;
    Ok(lines.join("\n"))
}
fn append_scope(
    index: &Index<'_>,
    presentation: &Presentation<'_>,
    scope: &str,
    prefix: &str,
    options: &RenderOptions,
    lines: &mut Vec<String>,
) -> Result<(), DocumentError> {
    let children = &index.scopes[scope].nodes;
    for (position, id) in children.iter().enumerate() {
        let last = position + 1 == children.len();
        let node = index.node(id)?;
        let branch = if last { "└─ " } else { "├─ " };
        let mut label = node_label(node, presentation, options);
        let (mut after, requires) = incoming(index, presentation, id, options)?;
        append_condition_inputs(index, presentation, node, options, &mut after)?;
        if !after.is_empty() {
            append(&mut label, format_args!(" [after: {}]", after.join(", ")));
        }
        if !requires.is_empty() {
            append(
                &mut label,
                format_args!(" [requires: {}]", requires.join(", ")),
            );
        }
        lines.push(format!("{prefix}{branch}{label}"));
        if let Node::Subgraph(child) = node {
            append_scope(
                index,
                presentation,
                &child.child_graph,
                &format!("{prefix}{}", if last { "   " } else { "│  " }),
                options,
                lines,
            )?;
        }
    }
    Ok(())
}
fn node_label(node: &Node, presentation: &Presentation<'_>, options: &RenderOptions) -> String {
    let id = node.id();
    let mut label = node.label().to_owned();
    if matches!(node, Node::Subgraph(_)) {
        label.push_str(" [graph]");
    } else if matches!(node,Node::Leaf(step) if step.action_kind==ActionKind::Native) {
        label.push_str(" [native]");
    }
    for condition in presentation.static_nodes.get(id).into_iter().flatten() {
        append(
            &mut label,
            format_args!(
                " [static: {}]",
                options
                    .condition_labels
                    .get(*condition)
                    .map_or(*condition, String::as_str)
            ),
        );
    }
    for condition in presentation.selections.get(id).into_iter().flatten() {
        append(
            &mut label,
            format_args!(" [select: {}]", static_value(condition)),
        );
    }
    if let Some(condition) = node.condition() {
        append(
            &mut label,
            format_args!(" [condition: {}]", condition_label(condition, options)),
        );
    }
    label
}
fn incoming(
    index: &Index<'_>,
    presentation: &Presentation<'_>,
    id: &str,
    options: &RenderOptions,
) -> Result<(Vec<String>, Vec<String>), DocumentError> {
    let mut after = Vec::new();
    let mut requires = Vec::new();
    for edge in index.incoming.get(id).into_iter().flatten() {
        let mut source = index.node(edge.source())?.label().to_owned();
        for condition in presentation
            .static_edges
            .get(&(edge.source(), id))
            .into_iter()
            .flatten()
        {
            append(
                &mut source,
                format_args!(
                    " (static: {})",
                    options
                        .condition_labels
                        .get(*condition)
                        .map_or(*condition, String::as_str)
                ),
            );
        }
        match edge {
            Edge::Conditional(edge) => append(
                &mut source,
                format_args!(" (条件边: {})", condition_label(&edge.condition, options)),
            ),
            Edge::Result(edge) => {
                if let ResultSource::Port { name, .. } = &edge.source {
                    append(&mut source, format_args!(".{}", port_name(name.as_deref())));
                }
                append(&mut source, format_args!(" ({})", result_label(edge)));
            }
            Edge::Port(edge) => {
                let mut value = format!(
                    "{} <- {} @ {}",
                    port_name(edge.target_port.as_deref()),
                    port_name(edge.source_port.as_deref()),
                    index.node(&edge.source)?.label()
                );
                if let Some(condition) = &edge.condition {
                    append(
                        &mut value,
                        format_args!(" (条件边: {})", condition_label(condition, options)),
                    );
                }
                requires.push(value);
            }
            Edge::Order(_) => (),
        }
        after.push(source);
    }
    Ok((after, requires))
}
fn append_condition_inputs(
    index: &Index<'_>,
    presentation: &Presentation<'_>,
    node: &Node,
    options: &RenderOptions,
    after: &mut Vec<String>,
) -> Result<(), DocumentError> {
    let mut conditions = Vec::new();
    if let Some(condition) = node.condition() {
        conditions.push((condition, "condition".to_owned()));
    }
    for edge in index.incoming.get(node.id()).into_iter().flatten() {
        if let Some(condition) = edge.condition() {
            conditions.push((
                condition,
                format!("edge condition {}", condition_label(condition, options)),
            ));
        }
    }
    let mut seen = std::collections::HashSet::new();
    for (condition, receiver) in conditions {
        if seen.insert(condition) {
            for (position, source) in presentation.condition_inputs[condition].iter().enumerate() {
                after.push(format!(
                    "{} ({receiver} input: {position})",
                    index.node(source)?.label()
                ));
            }
        }
    }
    Ok(())
}
fn render_compiled(
    index: &Index<'_>,
    presentation: &Presentation<'_>,
    title: &str,
    options: &RenderOptions,
) -> String {
    let mut lines = vec![format!("compilation: {title}"), "compiled_steps".into()];
    for graph_nodes in [false, true] {
        let nodes = index
            .view
            .nodes
            .iter()
            .filter(|node| matches!(node, Node::Subgraph(_)) == graph_nodes)
            .collect::<Vec<_>>();
        if graph_nodes && !nodes.is_empty() {
            lines.push("compiled_composites".into());
        }
        for node in nodes {
            let mut label = node.labels().join(".");
            if let Node::Subgraph(graph) = node {
                let children = index.scopes[graph.child_graph.as_str()]
                    .nodes
                    .iter()
                    .map(|id| index.nodes[id.as_str()].labels().join("."))
                    .collect::<Vec<_>>();
                append(
                    &mut label,
                    format_args!(
                        " [children: {}]",
                        if children.is_empty() {
                            "-".into()
                        } else {
                            children.join(", ")
                        }
                    ),
                );
            }
            if let Some(condition) = node.condition() {
                append(
                    &mut label,
                    format_args!(" [condition: {}]", condition_label(condition, options)),
                );
            }
            let after = presentation
                .dependencies
                .get(node.id())
                .into_iter()
                .flatten()
                .filter_map(|dep| match dep {
                    Dependency::Required { source, target } if target == node.id() => {
                        Some(index.nodes[source.as_str()].labels().join("."))
                    }
                    Dependency::Conditional {
                        source,
                        target,
                        condition,
                    } if target == node.id() => Some(format!(
                        "{} (条件边: {})",
                        index.nodes[source.as_str()].labels().join("."),
                        condition_label(condition, options)
                    )),
                    _ => None,
                })
                .collect::<Vec<_>>();
            if !after.is_empty() {
                append(&mut label, format_args!(" [after: {}]", after.join(", ")));
            }
            lines.push(format!("- {label}"));
        }
    }
    lines.join("\n")
}
