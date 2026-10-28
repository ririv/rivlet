//! Direct witnesses for failed and blocked execution transitions.

use crate::execution::ConditionId;
use topology::NodeId;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FailureCause {
    Action,
    Child(NodeId),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ConditionSite {
    Node(ConditionId),
    Edge {
        condition: ConditionId,
        prerequisite: NodeId,
    },
}

impl ConditionSite {
    #[must_use]
    pub const fn condition(self) -> ConditionId {
        match self {
            Self::Node(condition) | Self::Edge { condition, .. } => condition,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BlockCause {
    Prerequisite(NodeId),
    Condition(ConditionSite),
    ConditionInput {
        condition: ConditionSite,
        input: NodeId,
    },
    Parent(NodeId),
}
