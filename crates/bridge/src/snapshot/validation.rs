use std::collections::HashSet;

use action::ActionError;

use super::{EdgeInfo, NodeKind, PrerequisiteInfo, ScopeInfo, Site, Snapshot};

fn invalid() -> ActionError {
    ActionError::new("native graph descriptor contains an invalid reference or ownership")
}

impl Snapshot {
    pub(crate) fn validate(&self) -> Result<(), ActionError> {
        if self.scopes.is_empty() || self.scopes[0].owner.is_some() {
            return Err(invalid());
        }
        self.validate_nodes()?;
        let mut seen = HashSet::new();
        for (index, scope) in self.scopes.iter().enumerate() {
            let prefix = match scope.owner {
                None if index == 0 => &[][..],
                Some(owner) => {
                    let node = self.nodes.get(owner).ok_or_else(invalid)?;
                    if !matches!(node.kind, NodeKind::Graph { scope } if scope == index) {
                        return Err(invalid());
                    }
                    &node.path
                }
                None => return Err(invalid()),
            };
            let mut identities = HashSet::new();
            for (local, id) in scope.nodes.iter().copied().enumerate() {
                let node = self.nodes.get(id).ok_or_else(invalid)?;
                if node.graph != index
                    || !seen.insert(id)
                    || !identities.insert(node.identity)
                    || node.path.len() != prefix.len() + 1
                    || !node.path.starts_with(prefix)
                    || node.path.last() != Some(&local)
                {
                    return Err(invalid());
                }
            }
            self.validate_interface(scope, prefix)?;
            self.validate_edges(scope)?;
        }
        if seen.len() != self.nodes.len() {
            return Err(invalid());
        }
        self.validate_conditions()?;
        for port in &self.ports {
            if port.source >= self.nodes.len()
                || port.target >= self.nodes.len()
                || port.condition.is_some_and(|id| id >= self.conditions.len())
            {
                return Err(invalid());
            }
        }
        Ok(())
    }

    fn validate_nodes(&self) -> Result<(), ActionError> {
        for (index, node) in self.nodes.iter().enumerate() {
            if node.graph >= self.scopes.len()
                || node.inputs.iter().any(|source| *source >= self.nodes.len())
                || node.condition.is_some_and(|id| id >= self.conditions.len())
            {
                return Err(invalid());
            }
            if let NodeKind::Graph { scope } = node.kind {
                if self
                    .scopes
                    .get(scope)
                    .is_none_or(|scope| scope.owner != Some(index))
                {
                    return Err(invalid());
                }
            }
            for prerequisite in &node.prerequisites {
                let (source, condition) = match *prerequisite {
                    PrerequisiteInfo::Required { node } => (node, None),
                    PrerequisiteInfo::Conditional { node, condition } => (node, Some(condition)),
                };
                if source >= self.nodes.len()
                    || condition.is_some_and(|id| id >= self.conditions.len())
                {
                    return Err(invalid());
                }
            }
        }
        Ok(())
    }

    fn validate_interface(&self, scope: &ScopeInfo, prefix: &[usize]) -> Result<(), ActionError> {
        let mut names = HashSet::new();
        for (name, nodes) in &scope.inputs {
            if !names.insert(name)
                || name
                    .as_ref()
                    .is_some_and(|name| name.is_empty() || name.trim() != name)
                || nodes.is_empty()
            {
                return Err(invalid());
            }
            for node in nodes {
                self.validate_descendant(*node, prefix)?;
            }
        }
        names.clear();
        for (name, node) in &scope.outputs {
            if !names.insert(name)
                || name
                    .as_ref()
                    .is_some_and(|name| name.is_empty() || name.trim() != name)
            {
                return Err(invalid());
            }
            self.validate_descendant(*node, prefix)?;
        }
        Ok(())
    }

    fn validate_descendant(&self, node: usize, prefix: &[usize]) -> Result<(), ActionError> {
        let node = self.nodes.get(node).ok_or_else(invalid)?;
        if node.path.len() <= prefix.len() || !node.path.starts_with(prefix) {
            return Err(invalid());
        }
        Ok(())
    }

    fn validate_edges(&self, scope: &ScopeInfo) -> Result<(), ActionError> {
        for edge in &scope.edges {
            let (source, target, condition, source_port, target_port) = match edge {
                EdgeInfo::Order {
                    source,
                    target,
                    condition,
                } => (*source, *target, *condition, false, false),
                EdgeInfo::Ports {
                    source,
                    target,
                    condition,
                    ..
                } => (*source, *target, *condition, true, true),
                EdgeInfo::Result {
                    source,
                    target,
                    output,
                    input,
                    ..
                } => (*source, *target, None, output.is_some(), input.is_some()),
            };
            let source = scope.nodes.get(source).ok_or_else(invalid)?;
            let target = scope.nodes.get(target).ok_or_else(invalid)?;
            if condition.is_some_and(|id| id >= self.conditions.len())
                || (source_port && !matches!(self.nodes[*source].kind, NodeKind::Graph { .. }))
                || (target_port && !matches!(self.nodes[*target].kind, NodeKind::Graph { .. }))
            {
                return Err(invalid());
            }
        }
        Ok(())
    }

    fn validate_conditions(&self) -> Result<(), ActionError> {
        for (id, condition) in self.conditions.iter().enumerate() {
            if condition.inputs.len() != condition.types.len()
                || condition
                    .inputs
                    .iter()
                    .any(|node| *node >= self.nodes.len())
            {
                return Err(invalid());
            }
            let target = self
                .nodes
                .get(condition.site.target())
                .ok_or_else(invalid)?;
            match condition.site {
                Site::Node { .. } if target.condition == Some(id) => {}
                Site::Edge { index, .. } => {
                    let edge = self.scopes[target.graph]
                        .edges
                        .get(index)
                        .ok_or_else(invalid)?;
                    let (edge_target, edge_condition) = match edge {
                        EdgeInfo::Order {
                            target, condition, ..
                        }
                        | EdgeInfo::Ports {
                            target, condition, ..
                        } => (*target, *condition),
                        EdgeInfo::Result { .. } => return Err(invalid()),
                    };
                    if edge_condition != Some(id)
                        || self.scopes[target.graph].nodes[edge_target] != condition.site.target()
                    {
                        return Err(invalid());
                    }
                }
                Site::Node { .. } => return Err(invalid()),
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::Type;
    use crate::snapshot::NodeInfo;

    fn fixture() -> Snapshot {
        Snapshot {
            nodes: vec![NodeInfo {
                identity: 1,
                label: "task".to_owned(),
                graph: 0,
                path: vec![0],
                kind: NodeKind::Step {
                    inputs: vec![],
                    output: Type::Unit,
                },
                prerequisites: vec![],
                inputs: vec![],
                condition: None,
            }],
            scopes: vec![ScopeInfo {
                owner: None,
                nodes: vec![0],
                inputs: vec![(None, vec![0])],
                outputs: vec![(None, 0)],
                edges: vec![],
            }],
            conditions: vec![],
            ports: vec![],
        }
    }

    #[test]
    fn serialized_snapshot_preserves_valid_ownership() {
        let bytes = serde_json::to_vec(&fixture()).unwrap();
        serde_json::from_slice::<Snapshot>(&bytes)
            .unwrap()
            .validate()
            .unwrap();
    }

    #[test]
    fn transport_rejects_invalid_paths_ports_and_scope_membership() {
        let mut path = fixture();
        path.nodes[0].path = vec![1];
        assert!(path.validate().is_err());
        let mut duplicate = fixture();
        duplicate.scopes[0].outputs.push((None, 0));
        assert!(duplicate.validate().is_err());
        let mut membership = fixture();
        membership.scopes[0].nodes.clear();
        assert!(membership.validate().is_err());
        let mut edge = fixture();
        edge.scopes[0].edges.push(EdgeInfo::Order {
            source: 2,
            target: 0,
            condition: None,
        });
        assert!(edge.validate().is_err());
    }
}
