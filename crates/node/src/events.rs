//! Lossless direct failure witnesses in a private tagged wire protocol.

use rivlet::{
    BlockCause, ConditionSite, ExecutionBatch, ExecutionEvent, FailureCause, SessionStatus,
};
use serde_json::{Value, json};

fn site(value: ConditionSite) -> Value {
    match value {
        ConditionSite::Node(condition) => json!({"kind":"node", "condition":condition.index()}),
        ConditionSite::Edge {
            condition,
            prerequisite,
        } => {
            json!({"kind":"edge", "condition":condition.index(), "prerequisite":prerequisite.index()})
        }
    }
}

fn event(value: &ExecutionEvent) -> Value {
    match *value {
        ExecutionEvent::CancellationRequested => json!({"kind":"cancellationRequested"}),
        ExecutionEvent::ConditionEvaluated { node, value } => {
            json!({"kind":"conditionEvaluated", "node":node.index(), "value":value})
        }
        ExecutionEvent::ConditionSkipped(node) => {
            json!({"kind":"conditionSkipped", "node":node.index()})
        }
        ExecutionEvent::CompositeEntered(node) => {
            json!({"kind":"graphEntered", "node":node.index()})
        }
        ExecutionEvent::CompositeCompleted(node) => {
            json!({"kind":"graphCompleted", "node":node.index()})
        }
        ExecutionEvent::NodeSkipped(node) => json!({"kind":"skipped", "node":node.index()}),
        ExecutionEvent::NodeCancelled(node) => json!({"kind":"cancelled", "node":node.index()}),
        ExecutionEvent::NodeFailed { node, cause } => {
            let cause = match cause {
                FailureCause::Action => json!({"kind":"action"}),
                FailureCause::Child(child) => json!({"kind":"child", "node":child.index()}),
            };
            json!({"kind":"failed", "node":node.index(), "cause":cause})
        }
        ExecutionEvent::NodeBlocked { node, cause } => {
            let cause = match cause {
                BlockCause::Prerequisite(source) => {
                    json!({"kind":"prerequisite", "node":source.index()})
                }
                BlockCause::Parent(parent) => json!({"kind":"parent", "node":parent.index()}),
                BlockCause::Condition(condition) => {
                    json!({"kind":"condition", "site":site(condition)})
                }
                BlockCause::ConditionInput { condition, input } => {
                    json!({"kind":"conditionInput", "site":site(condition), "input":input.index()})
                }
            };
            json!({"kind":"blocked", "node":node.index(), "cause":cause})
        }
    }
}

pub(crate) fn batch(value: &ExecutionBatch) -> Value {
    let status = match value.status() {
        SessionStatus::Running => "running",
        SessionStatus::Finished => "succeeded",
        SessionStatus::Failed => "failed",
        SessionStatus::Cancelled => "cancelled",
    };
    json!({
        "actions":value.actions().iter().map(|node|node.index()).collect::<Vec<_>>(),
        "conditions":value.conditions().iter().map(|item|json!({"node":item.node().index(), "condition":item.condition().index()})).collect::<Vec<_>>(),
        "events":value.events().iter().map(event).collect::<Vec<_>>(),
        "status":status,
    })
}
