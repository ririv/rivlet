//! Compile-time activation declarations, separate from runtime predicates.

use crate::{Edge, Graph, GraphError, Node};
use rivlet::NodeIdentity;
use std::collections::{HashMap, HashSet};
use std::sync::Arc;

#[derive(Clone)]
pub enum StaticCondition {
    Known(bool),
    Evaluate(Arc<dyn Fn() -> bool + Send + Sync>),
}
impl From<bool> for StaticCondition {
    fn from(value: bool) -> Self {
        Self::Known(value)
    }
}
impl std::fmt::Debug for StaticCondition {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Known(value) => f.debug_tuple("Known").field(value).finish(),
            Self::Evaluate(_) => f.write_str("Evaluate(..)"),
        }
    }
}
impl StaticCondition {
    #[must_use]
    pub fn evaluate(predicate: impl Fn() -> bool + Send + Sync + 'static) -> Self {
        Self::Evaluate(Arc::new(predicate))
    }
    fn value(&self) -> Result<bool, StaticError> {
        match self {
            Self::Known(value) => Ok(*value),
            Self::Evaluate(predicate) => {
                std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| predicate()))
                    .map_err(|_| StaticError::PredicatePanicked)
            }
        }
    }
}

#[derive(Clone, Debug)]
pub struct Case {
    condition: StaticCondition,
    node: NodeIdentity,
}
impl Case {
    #[must_use]
    pub fn new(condition: impl Into<StaticCondition>, node: &(impl Node + ?Sized)) -> Self {
        Self {
            condition: condition.into(),
            node: node.id(),
        }
    }
}

#[derive(Clone, Debug)]
pub enum StaticRule {
    When(Case),
    Edge {
        condition: StaticCondition,
        source: NodeIdentity,
        target: NodeIdentity,
    },
    Select(Arc<[Case]>),
}
impl StaticRule {
    #[must_use]
    pub fn when(condition: impl Into<StaticCondition>, node: &(impl Node + ?Sized)) -> Self {
        Self::When(Case::new(condition, node))
    }
    #[must_use]
    pub fn edge(
        condition: impl Into<StaticCondition>,
        source: &(impl Node + ?Sized),
        target: &(impl Node + ?Sized),
    ) -> Self {
        Self::Edge {
            condition: condition.into(),
            source: source.id(),
            target: target.id(),
        }
    }
    #[must_use]
    pub fn select(cases: impl IntoIterator<Item = Case>) -> Self {
        Self::Select(cases.into_iter().collect())
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum StaticError {
    PredicatePanicked,
    UnknownNode(NodeIdentity),
    DuplicateNode(NodeIdentity),
    UnknownEdge {
        source: NodeIdentity,
        target: NodeIdentity,
    },
    DuplicateEdge(usize),
    ResultEdge(usize),
    SelectionCount(usize),
}
impl std::fmt::Display for StaticError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "invalid static declaration: {self:?}")
    }
}
impl std::error::Error for StaticError {}

pub(crate) struct Bindings {
    pub nodes: HashMap<NodeIdentity, bool>,
    pub edges: HashMap<usize, bool>,
}
impl Bindings {
    pub fn evaluate(graph: &Graph) -> Result<Self, GraphError> {
        if graph.static_rules().is_empty() {
            return Ok(Self {
                nodes: HashMap::new(),
                edges: HashMap::new(),
            });
        }
        let known: HashSet<_> = graph.nodes().iter().map(Node::id).collect();
        let mut bindings = Self {
            nodes: HashMap::new(),
            edges: HashMap::new(),
        };
        let require = |node| {
            if known.contains(&node) {
                Ok(())
            } else {
                Err(StaticError::UnknownNode(node))
            }
        };
        for rule in graph.static_rules() {
            match rule {
                StaticRule::When(case) => {
                    require(case.node)?;
                    bindings.bind(case.node, case.condition.value()?)?;
                }
                StaticRule::Select(cases) => {
                    let mut values = Vec::with_capacity(cases.len());
                    for case in cases.iter() {
                        require(case.node)?;
                        values.push(case.condition.value()?);
                    }
                    let count = values.iter().filter(|value| **value).count();
                    if count != 1 {
                        return Err(StaticError::SelectionCount(count).into());
                    }
                    for (case, value) in cases.iter().zip(values) {
                        bindings.bind(case.node, value)?;
                    }
                }
                StaticRule::Edge {
                    condition,
                    source,
                    target,
                } => {
                    require(*source)?;
                    require(*target)?;
                    let enabled = condition.value()?;
                    let mut found = false;
                    for (index, edge) in graph.edges().iter().enumerate() {
                        if edge.target() != *target || !edge.sources().contains(source) {
                            continue;
                        }
                        if matches!(edge, Edge::Results { .. }) {
                            return Err(StaticError::ResultEdge(index).into());
                        }
                        if bindings.edges.insert(index, enabled).is_some() {
                            return Err(StaticError::DuplicateEdge(index).into());
                        }
                        found = true;
                    }
                    if !found {
                        return Err(StaticError::UnknownEdge {
                            source: *source,
                            target: *target,
                        }
                        .into());
                    }
                }
            }
        }
        Ok(bindings)
    }
    fn bind(&mut self, node: NodeIdentity, value: bool) -> Result<(), StaticError> {
        if self.nodes.insert(node, value).is_some() {
            return Err(StaticError::DuplicateNode(node));
        }
        Ok(())
    }
}
