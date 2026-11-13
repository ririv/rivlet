use std::collections::{HashMap, HashSet};
use std::error::Error;
use std::fmt::{self, Display, Formatter};

use topology::{Arc, DagTopology, DirectedTopology, NodeId, TopologyError};

/// Stable identity of one opaque runtime predicate inside a compiled graph.
#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct ConditionId(usize);

impl ConditionId {
    #[must_use]
    pub const fn new(index: usize) -> Self {
        Self(index)
    }

    #[must_use]
    pub const fn index(self) -> usize {
        self.0
    }
}

/// Result dependencies that must finish before an opaque predicate is evaluated.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RuntimeCondition {
    reads: Vec<NodeId>,
}

impl RuntimeCondition {
    #[must_use]
    pub fn new(reads: impl IntoIterator<Item = NodeId>) -> Self {
        Self {
            reads: reads.into_iter().collect(),
        }
    }

    #[must_use]
    pub fn reads(&self) -> &[NodeId] {
        &self.reads
    }
}

/// A prerequisite whose conditional branch is represented without boolean flags.
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub enum Prerequisite {
    Required(NodeId),
    Conditional {
        node: NodeId,
        condition: ConditionId,
    },
}

impl Prerequisite {
    #[must_use]
    pub const fn node(self) -> NodeId {
        match self {
            Self::Required(node) | Self::Conditional { node, .. } => node,
        }
    }

    #[must_use]
    pub const fn condition(self) -> Option<ConditionId> {
        match self {
            Self::Required(_) => None,
            Self::Conditional { condition, .. } => Some(condition),
        }
    }
}

/// Mutually exclusive execution behavior of a compiled node.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ExecutionNodeKind {
    Step,
    Composite,
}

/// A resolved port connection between two declaration nodes.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct PortConnection {
    prerequisite: Prerequisite,
    target: NodeId,
}

impl PortConnection {
    #[must_use]
    pub const fn new(source: NodeId, target: NodeId) -> Self {
        Self {
            prerequisite: Prerequisite::Required(source),
            target,
        }
    }

    #[must_use]
    pub const fn conditional(source: NodeId, target: NodeId, condition: ConditionId) -> Self {
        Self {
            prerequisite: Prerequisite::Conditional {
                node: source,
                condition,
            },
            target,
        }
    }

    #[must_use]
    pub const fn source(self) -> NodeId {
        self.prerequisite.node()
    }

    #[must_use]
    pub const fn prerequisite(self) -> Prerequisite {
        self.prerequisite
    }

    #[must_use]
    pub const fn target(self) -> NodeId {
        self.target
    }
}

/// Structural facts needed by the compiled scheduler.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExecutionNode {
    kind: ExecutionNodeKind,
    owner: Option<NodeId>,
    prerequisites: Vec<Prerequisite>,
    condition: Option<ConditionId>,
}

impl ExecutionNode {
    #[must_use]
    pub fn step(
        owner: Option<NodeId>,
        prerequisites: impl IntoIterator<Item = Prerequisite>,
        condition: Option<ConditionId>,
    ) -> Self {
        Self {
            kind: ExecutionNodeKind::Step,
            owner,
            prerequisites: prerequisites.into_iter().collect(),
            condition,
        }
    }

    #[must_use]
    pub fn composite(
        owner: Option<NodeId>,
        prerequisites: impl IntoIterator<Item = Prerequisite>,
        condition: Option<ConditionId>,
    ) -> Self {
        Self {
            kind: ExecutionNodeKind::Composite,
            owner,
            prerequisites: prerequisites.into_iter().collect(),
            condition,
        }
    }

    #[must_use]
    pub const fn kind(&self) -> ExecutionNodeKind {
        self.kind
    }

    #[must_use]
    pub const fn owner(&self) -> Option<NodeId> {
        self.owner
    }

    #[must_use]
    pub fn prerequisites(&self) -> &[Prerequisite] {
        &self.prerequisites
    }

    #[must_use]
    pub const fn condition(&self) -> Option<ConditionId> {
        self.condition
    }
}

/// A compiled execution graph construction failure.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ExecutionGraphError {
    UnknownOwner {
        node: NodeId,
        owner: NodeId,
    },
    StepOwner {
        node: NodeId,
        owner: NodeId,
    },
    UnknownPrerequisite {
        node: NodeId,
        prerequisite: NodeId,
    },
    UnknownCondition {
        node: NodeId,
        condition: ConditionId,
    },
    UnknownConditionRead {
        condition: ConditionId,
        read: NodeId,
    },
    UnknownPortSource(NodeId),
    UnknownPortTarget(NodeId),
    OwnershipCycle(Vec<NodeId>),
    DependencyCycle(Vec<NodeId>),
}

impl Display for ExecutionGraphError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        match self {
            Self::UnknownOwner { node, owner } => write!(
                formatter,
                "execution node {} has unknown owner {}",
                node.index(),
                owner.index()
            ),
            Self::StepOwner { node, owner } => write!(
                formatter,
                "execution node {} has step owner {}",
                node.index(),
                owner.index()
            ),
            Self::UnknownPrerequisite { node, prerequisite } => write!(
                formatter,
                "execution node {} has unknown prerequisite {}",
                node.index(),
                prerequisite.index()
            ),
            Self::UnknownCondition { node, condition } => write!(
                formatter,
                "execution node {} has unknown condition {}",
                node.index(),
                condition.index()
            ),
            Self::UnknownConditionRead { condition, read } => write!(
                formatter,
                "runtime condition {} reads unknown node {}",
                condition.index(),
                read.index()
            ),
            Self::UnknownPortSource(node) => write!(
                formatter,
                "execution graph port has unknown source {}",
                node.index()
            ),
            Self::UnknownPortTarget(node) => write!(
                formatter,
                "execution graph port has unknown target {}",
                node.index()
            ),
            Self::OwnershipCycle(cycle) => {
                write_cycle(formatter, "execution graph has ownership cycle", cycle)
            }
            Self::DependencyCycle(cycle) => {
                write_cycle(formatter, "execution graph has dependency cycle", cycle)
            }
        }
    }
}

impl Error for ExecutionGraphError {}

fn write_cycle(formatter: &mut Formatter<'_>, prefix: &str, cycle: &[NodeId]) -> fmt::Result {
    write!(formatter, "{prefix}: ")?;
    let mut separator = "";
    for node in cycle {
        write!(formatter, "{separator}{}", node.index())?;
        separator = " -> ";
    }
    Ok(())
}

/// Fully indexed structural graph consumed by the compiled scheduler.
///
/// `owner` on each node is the only hierarchy declaration. Roots and composite
/// children are derived once during construction and cannot disagree with it.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CompiledExecutionGraph {
    nodes: Vec<ExecutionNode>,
    conditions: Vec<RuntimeCondition>,
    roots: Vec<NodeId>,
    children: Vec<Vec<NodeId>>,
    dependents: Vec<Vec<NodeId>>,
    dependency_topology: DagTopology,
}

impl CompiledExecutionGraph {
    /// Validates and indexes a compiled execution graph.
    ///
    /// # Errors
    ///
    /// Returns [`ExecutionGraphError`] when a node reference is invalid, an
    /// owner is not a composite, or ownership/dependency relations contain a
    /// cycle.
    pub fn new(
        nodes: impl IntoIterator<Item = ExecutionNode>,
        conditions: impl IntoIterator<Item = RuntimeCondition>,
    ) -> Result<Self, ExecutionGraphError> {
        Self::lower(nodes, conditions, [])
    }

    /// Lowers resolved port connections into ordinary prerequisites, then
    /// validates and indexes the resulting execution graph.
    ///
    /// # Errors
    ///
    /// Returns [`ExecutionGraphError`] when a port endpoint or another
    /// structural reference is invalid, or when lowering introduces a cycle.
    pub fn lower(
        nodes: impl IntoIterator<Item = ExecutionNode>,
        conditions: impl IntoIterator<Item = RuntimeCondition>,
        port_connections: impl IntoIterator<Item = PortConnection>,
    ) -> Result<Self, ExecutionGraphError> {
        let mut nodes = nodes.into_iter().collect::<Vec<_>>();
        let conditions = conditions.into_iter().collect::<Vec<_>>();
        lower_port_connections(&mut nodes, port_connections)?;
        validate_condition_reads(nodes.len(), &conditions)?;

        let mut roots = Vec::new();
        let mut children = vec![Vec::new(); nodes.len()];
        let mut owner_arcs = Vec::new();
        for (index, node) in nodes.iter().enumerate() {
            let node_id = NodeId::new(index);
            let Some(owner) = node.owner else {
                roots.push(node_id);
                continue;
            };
            let Some(owner_node) = nodes.get(owner.index()) else {
                return Err(ExecutionGraphError::UnknownOwner {
                    node: node_id,
                    owner,
                });
            };
            if owner_node.kind != ExecutionNodeKind::Composite {
                return Err(ExecutionGraphError::StepOwner {
                    node: node_id,
                    owner,
                });
            }
            children[owner.index()].push(node_id);
            owner_arcs.push(Arc::new(owner, node_id));
        }
        validate_ownership(nodes.len(), owner_arcs)?;

        let mut dependency_arcs = Vec::new();
        let mut dependency_pairs = HashSet::new();
        let mut dependents = vec![Vec::new(); nodes.len()];
        for (index, node) in nodes.iter().enumerate() {
            let node_id = NodeId::new(index);
            for prerequisite in &node.prerequisites {
                let source = prerequisite.node();
                if source.index() >= nodes.len() {
                    return Err(ExecutionGraphError::UnknownPrerequisite {
                        node: node_id,
                        prerequisite: source,
                    });
                }
                add_dependency(
                    source,
                    node_id,
                    &mut dependency_pairs,
                    &mut dependency_arcs,
                    &mut dependents,
                );
                if let Some(condition_id) = prerequisite.condition() {
                    let condition = require_condition(node_id, condition_id, &conditions)?;
                    for read in condition.reads() {
                        add_dependency(
                            *read,
                            node_id,
                            &mut dependency_pairs,
                            &mut dependency_arcs,
                            &mut dependents,
                        );
                    }
                }
            }
            if let Some(condition_id) = node.condition {
                let condition = require_condition(node_id, condition_id, &conditions)?;
                for read in condition.reads() {
                    add_dependency(
                        *read,
                        node_id,
                        &mut dependency_pairs,
                        &mut dependency_arcs,
                        &mut dependents,
                    );
                }
            }
        }

        let directed = validated_topology(nodes.len(), dependency_arcs);
        let dependency_topology = DagTopology::new(directed).map_err(|error| match error {
            TopologyError::Cycle(cycle) => ExecutionGraphError::DependencyCycle(cycle),
            _ => unreachable!("execution dependency endpoints were validated"),
        })?;

        Ok(Self {
            nodes,
            conditions,
            roots,
            children,
            dependents,
            dependency_topology,
        })
    }

    #[must_use]
    pub fn nodes(&self) -> &[ExecutionNode] {
        &self.nodes
    }

    #[must_use]
    pub fn conditions(&self) -> &[RuntimeCondition] {
        &self.conditions
    }

    #[must_use]
    pub fn roots(&self) -> &[NodeId] {
        &self.roots
    }

    #[must_use]
    pub fn children(&self, composite: NodeId) -> Option<&[NodeId]> {
        self.children.get(composite.index()).map(Vec::as_slice)
    }

    #[must_use]
    pub fn dependents(&self, node: NodeId) -> Option<&[NodeId]> {
        self.dependents.get(node.index()).map(Vec::as_slice)
    }

    #[must_use]
    pub const fn dependency_topology(&self) -> &DagTopology {
        &self.dependency_topology
    }
}

fn lower_port_connections(
    nodes: &mut [ExecutionNode],
    port_connections: impl IntoIterator<Item = PortConnection>,
) -> Result<(), ExecutionGraphError> {
    // B 条端口连接、相关目标原有 P 条前置：平均 O(B + P) 时间和空间。
    // 条件属于每条依赖；只去重完全相同的前置，不用无条件依赖覆盖条件声明。
    let mut indexed: HashMap<NodeId, HashSet<Prerequisite>> = HashMap::new();
    for binding in port_connections {
        if binding.source().index() >= nodes.len() {
            return Err(ExecutionGraphError::UnknownPortSource(binding.source()));
        }
        let Some(target) = nodes.get_mut(binding.target.index()) else {
            return Err(ExecutionGraphError::UnknownPortTarget(binding.target));
        };
        // 零个或一个前置的判重本来就是 O(1)，无需为稀疏目标分配集合。
        // 前置只追加；一旦进入索引路径，后续不会退回此分支。
        if target.prerequisites.len() <= 1 {
            let prerequisite = binding.prerequisite;
            if target.prerequisites.first() != Some(&prerequisite) {
                target.prerequisites.push(prerequisite);
            }
            continue;
        }
        let prerequisites = indexed
            .entry(binding.target)
            .or_insert_with(|| target.prerequisites.iter().copied().collect());
        if prerequisites.insert(binding.prerequisite) {
            target.prerequisites.push(binding.prerequisite);
        }
    }
    Ok(())
}

fn validate_condition_reads(
    node_count: usize,
    conditions: &[RuntimeCondition],
) -> Result<(), ExecutionGraphError> {
    for (index, condition) in conditions.iter().enumerate() {
        let condition_id = ConditionId::new(index);
        for read in condition.reads() {
            if read.index() >= node_count {
                return Err(ExecutionGraphError::UnknownConditionRead {
                    condition: condition_id,
                    read: *read,
                });
            }
        }
    }
    Ok(())
}

fn validate_ownership(node_count: usize, owner_arcs: Vec<Arc>) -> Result<(), ExecutionGraphError> {
    let directed = validated_topology(node_count, owner_arcs);
    DagTopology::new(directed)
        .map(|_| ())
        .map_err(|error| match error {
            TopologyError::Cycle(cycle) => ExecutionGraphError::OwnershipCycle(cycle),
            _ => unreachable!("execution owner endpoints were validated"),
        })
}

fn require_condition(
    node: NodeId,
    condition: ConditionId,
    conditions: &[RuntimeCondition],
) -> Result<&RuntimeCondition, ExecutionGraphError> {
    conditions
        .get(condition.index())
        .ok_or(ExecutionGraphError::UnknownCondition { node, condition })
}

fn add_dependency(
    source: NodeId,
    target: NodeId,
    pairs: &mut HashSet<(NodeId, NodeId)>,
    arcs: &mut Vec<Arc>,
    dependents: &mut [Vec<NodeId>],
) {
    if !pairs.insert((source, target)) {
        return;
    }
    arcs.push(Arc::new(source, target));
    dependents[source.index()].push(target);
}

fn validated_topology(node_count: usize, arcs: Vec<Arc>) -> DirectedTopology {
    match DirectedTopology::new(node_count, arcs) {
        Ok(topology) => topology,
        Err(error) => unreachable!("validated execution topology was invalid: {error}"),
    }
}

#[cfg(test)]
mod tests {
    use super::{
        CompiledExecutionGraph, ConditionId, ExecutionGraphError, ExecutionNode, ExecutionNodeKind,
        PortConnection, Prerequisite, RuntimeCondition,
    };
    use topology::NodeId;

    fn node(index: usize) -> NodeId {
        NodeId::new(index)
    }

    fn condition(index: usize) -> ConditionId {
        ConditionId::new(index)
    }

    #[test]
    fn graph_derives_roots_children_and_dependents() {
        let graph = CompiledExecutionGraph::new(
            [
                ExecutionNode::composite(None, [], None),
                ExecutionNode::step(Some(node(0)), [], None),
                ExecutionNode::step(
                    Some(node(0)),
                    [Prerequisite::Required(node(1))],
                    Some(condition(0)),
                ),
            ],
            [RuntimeCondition::new([node(1)])],
        )
        .unwrap();

        assert_eq!(graph.children(node(0)), Some([node(1), node(2)].as_slice()));
        assert_eq!(graph.dependents(node(1)), Some([node(2)].as_slice()));
        assert_eq!(graph.nodes()[0].kind(), ExecutionNodeKind::Composite);
    }

    #[test]
    fn graph_rejects_a_step_owner() {
        let result = CompiledExecutionGraph::new(
            [
                ExecutionNode::step(None, [], None),
                ExecutionNode::step(Some(node(0)), [], None),
            ],
            [],
        );

        assert_eq!(
            result,
            Err(ExecutionGraphError::StepOwner {
                node: node(1),
                owner: node(0),
            })
        );
    }

    #[test]
    fn graph_rejects_an_ownership_cycle() {
        let result = CompiledExecutionGraph::new(
            [
                ExecutionNode::composite(Some(node(1)), [], None),
                ExecutionNode::composite(Some(node(0)), [], None),
            ],
            [],
        );

        assert_eq!(
            result,
            Err(ExecutionGraphError::OwnershipCycle(vec![
                node(0),
                node(1),
                node(0),
            ]))
        );
    }

    #[test]
    fn graph_rejects_a_dependency_cycle_through_condition_reads() {
        let result = CompiledExecutionGraph::new(
            [
                ExecutionNode::step(None, [], Some(condition(0))),
                ExecutionNode::step(None, [Prerequisite::Required(node(0))], None),
            ],
            [RuntimeCondition::new([node(1)])],
        );

        assert_eq!(
            result,
            Err(ExecutionGraphError::DependencyCycle(vec![
                node(0),
                node(1),
                node(0),
            ]))
        );
    }

    #[test]
    fn graph_lowers_port_connection_to_required_dependency() {
        let graph = CompiledExecutionGraph::lower(
            [
                ExecutionNode::step(None, [], None),
                ExecutionNode::step(None, [], None),
            ],
            [],
            [PortConnection::new(node(0), node(1))],
        )
        .unwrap();

        assert_eq!(
            graph.nodes()[1].prerequisites(),
            [Prerequisite::Required(node(0))]
        );
        assert_eq!(graph.dependents(node(0)), Some([node(1)].as_slice()));
    }

    #[test]
    fn port_deduplication_preserves_order_and_conditional_dependencies() {
        let conditional = Prerequisite::Conditional {
            node: node(0),
            condition: condition(0),
        };
        let graph = CompiledExecutionGraph::lower(
            [
                ExecutionNode::step(None, [], None),
                ExecutionNode::step(None, [], None),
                ExecutionNode::step(None, [], None),
                ExecutionNode::step(None, [conditional, Prerequisite::Required(node(1))], None),
                ExecutionNode::step(None, [], None),
            ],
            [RuntimeCondition::new([])],
            [
                PortConnection::new(node(2), node(3)),
                PortConnection::new(node(1), node(3)),
                PortConnection::new(node(0), node(4)),
                PortConnection::new(node(0), node(3)),
                PortConnection::new(node(2), node(3)),
                PortConnection::new(node(0), node(4)),
            ],
        )
        .unwrap();
        assert_eq!(
            graph.nodes()[3].prerequisites(),
            [
                conditional,
                Prerequisite::Required(node(1)),
                Prerequisite::Required(node(2)),
                Prerequisite::Required(node(0))
            ]
        );
        assert_eq!(
            graph.nodes()[4].prerequisites(),
            [Prerequisite::Required(node(0))]
        );
    }
}
