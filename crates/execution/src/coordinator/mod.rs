//! Native execution coordination shared by language hosts, without owning their runtimes.
//!
//! Experimental, alongside the cross-language SDK and bridge. Enable the explicit
//! `experimental-mixed-execution` feature; the pure Rust driver does not require it.

mod bindings;
mod error;
mod pool;
mod runtime;

use action::{ActionError, Value, ValueType};
use rivlet::{ConditionId, ConditionRequest, ExecutionEvent, NodeId, SessionStatus};

pub use bindings::{ActionBinding, ActionDispatch, ConditionBinding, ConditionDispatch, Workers};
pub use error::CoordinationError;
pub use runtime::Coordinator;

/// The compiled use requesting a host-owned value, not a language-specific object.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum InputSite {
    Action(NodeId),
    Condition(ConditionId),
}

/// Explicit conversion at the owning language boundary; native values bypass this trait.
pub trait HostValues {
    /// Converts one successful host result for a concrete native receiver.
    ///
    /// # Errors
    /// Returns a missing-value or conversion error, attributed to that receiver.
    fn import(
        &mut self,
        source: NodeId,
        expected: ValueType,
        site: InputSite,
    ) -> Result<Value, ActionError>;
}

/// Native execution events retain their ordering relative to core lifecycle events.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CoordinationEvent {
    Scheduler(ExecutionEvent),
    ActionStarted(NodeId),
    ActionCompleted(NodeId),
}

/// An action failure and a predicate failure remain distinct until language adaptation.
#[derive(Debug)]
pub enum NativeFailure {
    Action {
        node: NodeId,
        error: ActionError,
    },
    Condition {
        node: NodeId,
        condition: ConditionId,
        error: ActionError,
    },
}

/// One exchange: only host-owned actions and predicates leave the coordinator.
pub struct CoordinationBatch {
    pub actions: Vec<NodeId>,
    pub conditions: Vec<ConditionRequest>,
    pub events: Vec<CoordinationEvent>,
    pub status: SessionStatus,
    pub native_running: usize,
    pub failures: Vec<NativeFailure>,
    pub results: Vec<(NodeId, Value)>,
}

impl CoordinationBatch {
    fn new() -> Self {
        Self {
            actions: vec![],
            conditions: vec![],
            events: vec![],
            status: SessionStatus::Running,
            native_running: 0,
            failures: vec![],
            results: vec![],
        }
    }
}
