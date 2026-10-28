//! Tagged tuple encoding for the private batched Python boundary.

use pyo3::prelude::*;
use rivlet::{BlockCause, ConditionSite, ExecutionEvent, FailureCause};

// 不为无载荷事件传占位字段；失败只携带索引，不跨语言传递异常或业务结果。
#[derive(Clone, IntoPyObject)]
pub(crate) enum EncodedEvent {
    Lifecycle(u8, usize),
    Condition(u8, usize, bool),
    Related(u8, usize, usize),
    ConditionLink(u8, usize, usize, usize),
    EdgeConditionInput(u8, usize, usize, usize, usize),
}

pub(crate) fn encode_event(event: &ExecutionEvent) -> EncodedEvent {
    match *event {
        ExecutionEvent::ConditionEvaluated { node, value } => {
            EncodedEvent::Condition(0, node.index(), value)
        }
        ExecutionEvent::ConditionSkipped(node) => EncodedEvent::Lifecycle(1, node.index()),
        ExecutionEvent::CompositeEntered(node) => EncodedEvent::Lifecycle(2, node.index()),
        ExecutionEvent::CompositeCompleted(node) => EncodedEvent::Lifecycle(3, node.index()),
        ExecutionEvent::NodeSkipped(node) => EncodedEvent::Lifecycle(4, node.index()),
        ExecutionEvent::NodeFailed {
            node,
            cause: FailureCause::Action,
        } => EncodedEvent::Lifecycle(7, node.index()),
        ExecutionEvent::NodeFailed {
            node,
            cause: FailureCause::Child(child),
        } => EncodedEvent::Related(12, node.index(), child.index()),
        ExecutionEvent::NodeBlocked { node, cause } => match cause {
            BlockCause::Prerequisite(source) => {
                EncodedEvent::Related(8, node.index(), source.index())
            }
            BlockCause::Condition(ConditionSite::Node(condition)) => {
                EncodedEvent::Related(9, node.index(), condition.index())
            }
            BlockCause::Condition(ConditionSite::Edge {
                condition,
                prerequisite,
            }) => EncodedEvent::ConditionLink(
                13,
                node.index(),
                condition.index(),
                prerequisite.index(),
            ),
            BlockCause::ConditionInput {
                condition: ConditionSite::Node(condition),
                input,
            } => EncodedEvent::ConditionLink(10, node.index(), condition.index(), input.index()),
            BlockCause::ConditionInput {
                condition:
                    ConditionSite::Edge {
                        condition,
                        prerequisite,
                    },
                input,
            } => EncodedEvent::EdgeConditionInput(
                14,
                node.index(),
                condition.index(),
                prerequisite.index(),
                input.index(),
            ),
            BlockCause::Parent(parent) => EncodedEvent::Related(11, node.index(), parent.index()),
        },
    }
}
