use crate::{ExecutionError, NodeFailure, NodeOutcome};
use action::Value;
use rivlet::{ExecutionEvent, FailureCause, NodeId};

pub(super) struct Results(Vec<Option<NodeOutcome>>);

impl Results {
    pub(super) fn new(count: usize) -> Self {
        Self((0..count).map(|_| None).collect())
    }

    pub(super) fn record(
        &mut self,
        node: NodeId,
        outcome: NodeOutcome,
    ) -> Result<(), ExecutionError> {
        let slot = &mut self.0[node.index()];
        if slot.is_some() {
            return Err(ExecutionError::DuplicateOutcome(node));
        }
        *slot = Some(outcome);
        Ok(())
    }

    pub(super) fn input(&self, source: NodeId) -> Result<Option<Value>, ExecutionError> {
        match &self.0[source.index()] {
            Some(NodeOutcome::Succeeded(value)) => Ok(Some(value.clone())),
            Some(NodeOutcome::Skipped) => Ok(None),
            Some(NodeOutcome::Failed(_) | NodeOutcome::Blocked(_) | NodeOutcome::Cancelled) => {
                Err(ExecutionError::InvalidTransition(source))
            }
            None => Err(ExecutionError::MissingResult(source)),
        }
    }

    pub(super) fn events(&mut self, events: &[ExecutionEvent]) -> Result<(), ExecutionError> {
        for event in events {
            match *event {
                ExecutionEvent::CancellationRequested
                | ExecutionEvent::CompositeEntered(_)
                | ExecutionEvent::ConditionEvaluated { .. }
                | ExecutionEvent::ConditionSkipped(_) => {}
                ExecutionEvent::CompositeCompleted(node) => {
                    self.record(node, NodeOutcome::Succeeded(Value::new(())))?;
                }
                ExecutionEvent::NodeSkipped(node) => self.record(node, NodeOutcome::Skipped)?,
                ExecutionEvent::NodeCancelled(node) => {
                    if self.0[node.index()].is_none() {
                        self.record(node, NodeOutcome::Cancelled)?;
                    }
                }
                ExecutionEvent::NodeBlocked { node, cause } => {
                    self.record(node, NodeOutcome::Blocked(cause))?;
                }
                ExecutionEvent::NodeFailed {
                    node,
                    cause: FailureCause::Child(child),
                } => self.record(node, NodeOutcome::Failed(NodeFailure::Child(child)))?,
                ExecutionEvent::NodeFailed {
                    node,
                    cause: FailureCause::Action,
                } => {
                    if !matches!(
                        self.0[node.index()],
                        Some(NodeOutcome::Failed(NodeFailure::Action(_)))
                    ) {
                        return Err(ExecutionError::InvalidTransition(node));
                    }
                }
            }
        }
        Ok(())
    }

    pub(super) fn finish(self) -> Result<Vec<NodeOutcome>, ExecutionError> {
        self.0
            .into_iter()
            .enumerate()
            .map(|(index, outcome)| {
                outcome.ok_or(ExecutionError::MissingResult(NodeId::new(index)))
            })
            .collect()
    }
}
