use crate::presentation::Presentation;
use crate::tree::{port_name, result_label, static_value};
use crate::validate::Index;
use crate::{Dependency, DocumentError, Edge, GraphDocument, Node, RenderOptions};
fn escape(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('"', "&quot;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('\n', "<br/>")
        .replace('\r', "")
}
fn token(prefix: &str, id: &str) -> String {
    use std::fmt::Write;
    let mut value = prefix.to_owned();
    for byte in id.bytes() {
        write!(value, "{byte:02x}").expect("write to string");
    }
    value
}
/// Renders a document using collision-free mounted IDs and escaped labels.
///
/// # Errors
/// Returns an error for an invalid document or dangling structural reference.
pub fn render_mermaid(
    document: &GraphDocument,
    options: &RenderOptions,
) -> Result<String, DocumentError> {
    let index = Index::new(document)?;
    let mut lines = vec!["flowchart TD".into()];
    if let GraphDocument::Compiled(compiled) = document {
        let mut nodes = index
            .view
            .nodes
            .iter()
            .filter(|n| matches!(n, Node::Leaf(_)))
            .collect::<Vec<_>>();
        nodes.extend(
            index
                .view
                .nodes
                .iter()
                .filter(|n| matches!(n, Node::Subgraph(_))),
        );
        let ids = nodes
            .iter()
            .enumerate()
            .map(|(i, node)| (node.id(), format!("compiled_{i}")))
            .collect::<std::collections::HashMap<_, _>>();
        let title = options
            .root_label
            .as_deref()
            .unwrap_or(&index.scopes[index.view.root].label);
        lines.push(format!(
            "    subgraph compiled_graph[\"{}\"]",
            escape(title)
        ));
        for node in &nodes {
            if matches!(node, Node::Leaf(_)) {
                lines.push(format!(
                    "        {}[\"{}\"]",
                    ids[node.id()],
                    escape(&node.labels().join("."))
                ));
            }
        }
        lines.push("    end".into());
        if nodes.iter().any(|node| matches!(node, Node::Subgraph(_))) {
            lines.push("    subgraph compiled_composites".into());
            for node in &nodes {
                if matches!(node, Node::Subgraph(_)) {
                    lines.push(format!(
                        "        {}[\"{} [composite]\"]",
                        ids[node.id()],
                        escape(&node.labels().join("."))
                    ));
                }
            }
            lines.push("    end".into());
        }
        for dependency in &compiled.dependencies {
            let (source, target) = match dependency {
                Dependency::Required { source, target }
                | Dependency::Conditional { source, target, .. } => (source, target),
            };
            lines.push(format!(
                "    {} --> {}",
                ids[source.as_str()],
                ids[target.as_str()]
            ));
        }
        for node in nodes {
            if let Node::Subgraph(graph) = node {
                for child in &index.scopes[graph.child_graph.as_str()].nodes {
                    lines.push(format!(
                        "    {} -.-> {}",
                        ids[node.id()],
                        ids[child.as_str()]
                    ));
                }
            }
        }
    } else {
        append_scope(
            &index,
            &Presentation::new(document),
            index.view.root,
            "    ",
            options,
            &mut lines,
        )?;
    }
    Ok(lines.join("\n"))
}
fn append_scope(
    index: &Index<'_>,
    presentation: &Presentation<'_>,
    id: &str,
    indent: &str,
    options: &RenderOptions,
    lines: &mut Vec<String>,
) -> Result<(), DocumentError> {
    let scope = index.scopes[id];
    let label = if id == index.view.root {
        options.root_label.as_deref().unwrap_or(&scope.label)
    } else {
        &scope.label
    };
    lines.push(format!(
        "{indent}subgraph {}[\"{}\"]",
        token("graph_", id),
        escape(label)
    ));
    for child in &scope.nodes {
        let node = index.node(child)?;
        let mut label = node.label().to_owned();
        for condition in presentation.selections.get(node.id()).into_iter().flatten() {
            use std::fmt::Write;
            write!(label, " [select: {}]", static_value(condition))
                .expect("writing to String is infallible");
        }
        lines.push(format!(
            "{indent}    {}[\"{}\"]",
            token("node_", child),
            escape(&label)
        ));
        if let Node::Subgraph(child) = node {
            append_scope(
                index,
                presentation,
                &child.child_graph,
                &format!("{indent}    "),
                options,
                lines,
            )?;
        }
    }
    for edge in presentation.edges.get(id).into_iter().flatten() {
        let label = match edge {
            Edge::Result(edge) => Some(result_label(edge).replace("action input:", "action input")),
            Edge::Port(edge) => Some(if edge.source_port == edge.target_port {
                port_name(edge.source_port.as_deref()).to_owned()
            } else {
                format!(
                    "{} -> {}",
                    port_name(edge.source_port.as_deref()),
                    port_name(edge.target_port.as_deref())
                )
            }),
            Edge::Conditional(_) => Some("conditional".into()),
            Edge::Order(_) => None,
        };
        lines.push(format!(
            "{indent}    {} -->{} {}",
            token("node_", edge.source()),
            label.map_or(String::new(), |label| format!("|\"{}\"|", escape(&label))),
            token("node_", edge.target())
        ));
    }
    for child in &scope.nodes {
        let node = index.node(child)?;
        let conditions = node
            .condition()
            .map(|condition| (condition, "condition"))
            .into_iter()
            .chain(
                index
                    .incoming
                    .get(child.as_str())
                    .into_iter()
                    .flatten()
                    .filter_map(|edge| {
                        edge.condition()
                            .map(|condition| (condition, "edge condition"))
                    }),
            );
        let mut seen = std::collections::HashSet::new();
        for (condition, receiver) in conditions {
            if !seen.insert(condition) {
                continue;
            }
            for (position, source) in presentation.condition_inputs[condition].iter().enumerate() {
                lines.push(format!(
                    "{indent}    {} -.->|\"{receiver} input {position}\"| {}",
                    token("node_", source),
                    token("node_", child)
                ));
            }
        }
    }
    lines.push(format!("{indent}end"));
    Ok(())
}
