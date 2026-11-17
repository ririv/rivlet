use crate::{DocumentError, Edge, GraphDocument, Node, Port, ResultSource, ResultTarget, View};
use std::collections::{HashMap, HashSet};

pub(crate) struct Index<'a> {
    pub view: View<'a>,
    pub nodes: HashMap<&'a str, &'a Node>,
    pub scopes: HashMap<&'a str, &'a crate::Scope>,
    pub incoming: HashMap<&'a str, Vec<&'a Edge>>,
    input_ports: HashMap<(&'a str, Option<&'a str>), &'a [String]>,
    output_ports: HashMap<(&'a str, Option<&'a str>), &'a str>,
}
fn error(message: &str) -> DocumentError {
    DocumentError(message.into())
}
impl GraphDocument {
    /// Checks schema references without reconstructing or executing a graph.
    ///
    /// # Errors
    /// Reports invalid ownership, ports, edges, conditions or compiled references.
    pub fn validate(&self) -> Result<(), DocumentError> {
        Index::new(self).map(|_| ())
    }
}
impl<'a> Index<'a> {
    pub fn new(document: &'a GraphDocument) -> Result<Self, DocumentError> {
        let view = document.view();
        let mut nodes = HashMap::new();
        let mut scopes = HashMap::new();
        for node in view.nodes {
            if nodes.insert(node.id(), node).is_some() {
                return Err(error("duplicate mounted node ID"));
            }
            if node.identity().parse::<u64>().is_err() {
                return Err(error("node_id must be a decimal u64 string"));
            }
        }
        for scope in view.graphs {
            if scopes.insert(scope.id.as_str(), scope).is_some() {
                return Err(error("duplicate scope ID"));
            }
        }
        let root = scopes
            .get(view.root)
            .ok_or_else(|| error("missing root scope"))?;
        if root.owner.as_deref() != view.parent {
            return Err(error("root owner and document parent disagree"));
        }
        if view.parent.is_some_and(|owner| nodes.contains_key(owner)) {
            return Err(error("document parent must be outside the exported scope"));
        }
        let mut seen = HashSet::new();
        let mut mounted = HashSet::new();
        let mut pending = vec![view.root];
        while let Some(id) = pending.pop() {
            if !seen.insert(id) {
                return Err(error("scope ownership contains a cycle or duplicate child"));
            }
            let scope = scopes.get(id).ok_or_else(|| error("unknown child scope"))?;
            for local in &scope.nodes {
                let node = nodes
                    .get(local.as_str())
                    .ok_or_else(|| error("unknown scoped node"))?;
                if node.graph() != id || !mounted.insert(local.as_str()) {
                    return Err(error("node scope membership is inconsistent"));
                }
                if let Node::Subgraph(graph) = node {
                    let child = scopes
                        .get(graph.child_graph.as_str())
                        .ok_or_else(|| error("missing child scope"))?;
                    if child.owner.as_deref() != Some(node.id()) {
                        return Err(error("child scope owner mismatch"));
                    }
                    pending.push(child.id.as_str());
                }
            }
        }
        if seen.len() != scopes.len() || mounted.len() != nodes.len() {
            return Err(error("unreachable scope or node"));
        }
        let mut input_ports = HashMap::new();
        let mut output_ports = HashMap::new();
        for scope in view.graphs {
            for port in &scope.ports {
                match port {
                    Port::Input { port, nodes } => {
                        input_ports.insert((scope.id.as_str(), port.as_deref()), nodes.as_slice());
                    }
                    Port::Output { port, node } => {
                        output_ports.insert((scope.id.as_str(), port.as_deref()), node.as_str());
                    }
                }
            }
        }
        let mut index = Self {
            view,
            nodes,
            scopes,
            incoming: HashMap::new(),
            input_ports,
            output_ports,
        };
        index.validate_ports()?;
        index.incoming = index.validate_edges()?;
        let condition_ids = index.validate_conditions(document)?;
        index.validate_variant(document, &condition_ids)?;
        Ok(index)
    }
    fn validate_ports(&self) -> Result<(), DocumentError> {
        for scope in self.view.graphs {
            let mut names = HashSet::new();
            for port in &scope.ports {
                let (direction, name, targets) = match port {
                    Port::Input { port, nodes } => {
                        ("input", port, nodes.iter().collect::<Vec<_>>())
                    }
                    Port::Output { port, node } => ("output", port, vec![node]),
                };
                if name.as_ref().is_some_and(String::is_empty) || !names.insert((direction, name)) {
                    return Err(error("empty or duplicate port name"));
                }
                for target in targets {
                    if !self.in_scope(scope.id.as_str(), target)? {
                        return Err(error("port endpoint is outside its owning graph"));
                    }
                }
            }
        }
        Ok(())
    }
    fn validate_edges(&self) -> Result<HashMap<&'a str, Vec<&'a Edge>>, DocumentError> {
        let mut incoming: HashMap<&str, Vec<&Edge>> = HashMap::new();
        let mut edge_ids = HashSet::new();
        for edge in self.view.edges {
            if !edge_ids.insert(edge.id()) {
                return Err(error("duplicate edge ID"));
            }
            let source = self.node(edge.source())?;
            let target = self.node(edge.target())?;
            if source.graph() != edge.graph() || target.graph() != edge.graph() {
                return Err(error(
                    "edge endpoints must be local; use graph ports for descendants",
                ));
            }
            match edge {
                Edge::Port(port) => {
                    self.output_port(
                        &port.source,
                        port.source_port.as_deref(),
                        &port.provider_node,
                    )?;
                    self.input_port(
                        &port.target,
                        port.target_port.as_deref(),
                        &port.consumer_nodes,
                    )?;
                }
                Edge::Result(result) => {
                    if let ResultSource::Port {
                        owner,
                        name,
                        provider,
                    } = &result.source
                    {
                        self.output_port(owner, name.as_deref(), provider)?;
                    }
                    match &result.target {
                        ResultTarget::Port {
                            owner,
                            name,
                            consumers,
                        } => {
                            self.input_port(owner, name.as_deref(), consumers)?;
                            for target in consumers {
                                self.result_receiver(target)?;
                            }
                        }
                        ResultTarget::Direct(target) => {
                            self.result_receiver(target)?;
                        }
                    }
                }
                _ => (),
            }
            incoming.entry(edge.target()).or_default().push(edge);
        }
        Ok(incoming)
    }
    fn result_receiver(&self, target: &str) -> Result<(), DocumentError> {
        if matches!(self.node(target)?, Node::Leaf(_)) {
            Ok(())
        } else {
            Err(error("result receiver is not a Step"))
        }
    }
    fn validate_conditions(
        &self,
        document: &'a GraphDocument,
    ) -> Result<HashSet<&'a str>, DocumentError> {
        let mut condition_ids = HashSet::new();
        for (id, inputs) in document.condition_inputs() {
            if !condition_ids.insert(id) {
                return Err(error("duplicate condition ID"));
            }
            for input in inputs {
                self.node(input)?;
            }
        }
        for node in self.view.nodes {
            if let Some(id) = node.condition() {
                if !condition_ids.contains(id) {
                    return Err(error("unknown node condition"));
                }
            }
        }
        for edge in self.view.edges {
            if let Some(id) = edge.condition() {
                if !condition_ids.contains(id) {
                    return Err(error("unknown edge condition"));
                }
            }
        }
        Ok(condition_ids)
    }
    fn validate_variant(
        &self,
        document: &GraphDocument,
        condition_ids: &HashSet<&str>,
    ) -> Result<(), DocumentError> {
        match document {
            GraphDocument::Declaration(declaration) => {
                for rule in &declaration.static_rules {
                    match rule {
                        crate::StaticRule::Node { node, .. } => {
                            self.node(node)?;
                        }
                        crate::StaticRule::Edge { source, target, .. } => {
                            self.node(source)?;
                            self.node(target)?;
                        }
                    }
                }
                for selection in &declaration.selections {
                    for case in &selection.cases {
                        self.node(&case.node)?;
                    }
                }
            }
            GraphDocument::Compiled(compiled) => {
                for (index, condition) in compiled.conditions.iter().enumerate() {
                    if condition.index != index {
                        return Err(error("compiled condition indexes must be contiguous"));
                    }
                }
                for dep in &compiled.dependencies {
                    match dep {
                        crate::Dependency::Required { source, target } => {
                            self.node(source)?;
                            self.node(target)?;
                        }
                        crate::Dependency::Conditional {
                            source,
                            target,
                            condition,
                        } => {
                            self.node(source)?;
                            self.node(target)?;
                            if !condition_ids.contains(condition.as_str()) {
                                return Err(error("unknown dependency condition"));
                            }
                        }
                    }
                }
            }
        }
        Ok(())
    }
    pub fn node(&self, id: &str) -> Result<&'a Node, DocumentError> {
        self.nodes
            .get(id)
            .copied()
            .ok_or_else(|| error("unknown node reference"))
    }
    fn in_scope(&self, scope: &str, id: &str) -> Result<bool, DocumentError> {
        let mut current = self.node(id)?.graph();
        loop {
            if current == scope {
                return Ok(true);
            }
            let owner = self
                .scopes
                .get(current)
                .ok_or_else(|| error("unknown scope"))?
                .owner
                .as_deref();
            match owner {
                Some(owner) if self.nodes.contains_key(owner) => {
                    current = self.node(owner)?.graph();
                }
                _ => return Ok(false),
            }
        }
    }
    fn child(&self, owner: &str) -> Result<&'a crate::Scope, DocumentError> {
        let Node::Subgraph(node) = self.node(owner)? else {
            return Err(error("port owner is not a graph"));
        };
        self.scopes
            .get(node.child_graph.as_str())
            .copied()
            .ok_or_else(|| error("missing port owner scope"))
    }
    fn output_port(
        &self,
        owner: &str,
        name: Option<&str>,
        provider: &str,
    ) -> Result<(), DocumentError> {
        let child = self.child(owner)?;
        if self
            .output_ports
            .get(&(child.id.as_str(), name))
            .is_some_and(|node| *node == provider)
        {
            Ok(())
        } else {
            Err(error("output port and resolved provider disagree"))
        }
    }
    fn input_port(
        &self,
        owner: &str,
        name: Option<&str>,
        consumers: &[String],
    ) -> Result<(), DocumentError> {
        if consumers.is_empty() {
            return Err(error("result/readiness port has no consumers"));
        }
        let child = self.child(owner)?;
        if self
            .input_ports
            .get(&(child.id.as_str(), name))
            .is_some_and(|nodes| *nodes == consumers)
        {
            Ok(())
        } else {
            Err(error("input port and resolved consumers disagree"))
        }
    }
}
