//! Declare and execute Rivlet graphs with typed Rust actions.
//!
//! ```
//! use rivlet::{Action, ActionContext, ActionError, ActionHandle, Execution,
//!     ExecutionOptions, ExecutionStatus, Graph, Step};
//!
//! struct Work;
//! impl Action for Work {
//!     type Inputs = ();
//!     type Output = u32;
//!     fn execute(&self, _: &ActionContext, (): ()) -> Result<u32, ActionError> {
//!         Ok(42)
//!     }
//! }
//!
//! # fn main() -> Result<(), Box<dyn std::error::Error>> {
//! let work = Step::new("work", ActionHandle::new(Work))?;
//! let graph = Graph::new("workflow", [work.into()], [])?;
//! let outcome = Execution::new(graph.compile()?, ExecutionOptions::new(2)?).run()?;
//! assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
//! # Ok(())
//! # }
//! ```

pub mod action;
pub mod execution;
pub mod graph;

pub use rivlet_core as core;

pub use action::{
    Action, ActionContext, ActionError, ActionHandle, ActionInputs, ProcessAction, Value, ValueType,
};
pub use execution::{
    ConditionOutcome, Execution, ExecutionError, ExecutionOptions, ExecutionOutcome,
    ExecutionStatus, NodeFailure, NodeOutcome,
};
pub use graph::{
    Compilation, Condition, Edge, Graph, GraphError, GraphInterface, GraphInterfaceView, HasNodeId,
    InputEndpoint, InputPort, Node, OutputEndpoint, OutputPort, PortName, ResultSources,
    ResultTarget, Step,
};
pub use rivlet_core::NodeId;
