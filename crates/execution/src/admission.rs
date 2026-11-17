//! Per-execution limits resolved against actual mounted graph nodes.

use graph::Compilation;
use rivlet::{ConcurrencyLimits, NodeId, NodeIdentity, SessionConcurrency};
use std::collections::HashMap;
use std::num::NonZeroUsize;

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ConcurrencyError {
    InvalidMaximum,
    DuplicateNode(NodeIdentity),
    UnknownNode(NodeIdentity),
    AmbiguousNode(NodeIdentity),
}
impl std::fmt::Display for ConcurrencyError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "invalid concurrency limit: {self:?}")
    }
}
impl std::error::Error for ConcurrencyError {}

pub(crate) struct Admission(Option<ConcurrencyLimits>);
impl Admission {
    pub(crate) fn resolve(
        compilation: &Compilation,
        requests: &[(NodeIdentity, NonZeroUsize)],
    ) -> Result<(Self, Option<SessionConcurrency>), crate::ExecutionError> {
        if requests.is_empty() {
            return Ok((Self(None), None));
        }
        let mut mounts: HashMap<NodeIdentity, Vec<NodeId>> = HashMap::new();
        for index in 0..compilation.graph().nodes().len() {
            let node = NodeId::new(index);
            mounts
                .entry(compilation.node(node).identity())
                .or_default()
                .push(node);
        }
        let mut owners = HashMap::new();
        for (scope, (identity, _)) in requests.iter().enumerate() {
            let mounted = mounts
                .get(identity)
                .ok_or(ConcurrencyError::UnknownNode(*identity))?;
            let [node] = mounted.as_slice() else {
                return Err(ConcurrencyError::AmbiguousNode(*identity).into());
            };
            if owners.insert(*node, scope).is_some() {
                return Err(ConcurrencyError::DuplicateNode(*identity).into());
            }
        }
        let memberships = (0..compilation.graph().nodes().len())
            .map(|index| {
                let mut current = Some(NodeId::new(index));
                let mut scopes = Vec::new();
                while let Some(node) = current {
                    if let Some(scope) = owners.get(&node) {
                        scopes.push(*scope);
                    }
                    current = compilation.graph().nodes()[node.index()].owner();
                }
                scopes
            })
            .collect();
        let limits = ConcurrencyLimits::new(requests.iter().map(|(_, maximum)| *maximum));
        let binding = limits
            .bind(memberships)
            .expect("validated scope membership");
        Ok((Self(Some(limits)), Some(binding)))
    }
    pub(crate) fn child(&self, count: usize, inherited: &[usize]) -> Option<SessionConcurrency> {
        self.0.as_ref().map(|limits| {
            limits
                .bind(vec![inherited.to_vec(); count])
                .expect("inherited scope membership")
        })
    }
}
