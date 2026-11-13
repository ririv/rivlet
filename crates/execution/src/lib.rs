//! A synchronous Rust host composing the shared scheduler, workers and typed results.
//!
//! Graph declarations and the low-level session/executor APIs remain independent.
//! An Execution is consumed by run; an Arc<Compilation> can be shared by separate
//! executions, each with its own workers and results. No Python runtime is involved.
//!
//! ```
//! use execution::{Execution, ExecutionOptions, ExecutionStatus};
//! use graph::GraphStructure;
//! # fn main() -> Result<(), Box<dyn std::error::Error>> {
//! let compilation = GraphStructure::new([], [])?.compile_structure()?;
//! let outcome = Execution::new(compilation, ExecutionOptions::new(4)?).run()?;
//! assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
//! # Ok(())
//! # }
//! ```

mod driver;
mod error;
mod outcome;

use std::num::NonZeroUsize;
use std::sync::Arc;

use graph::Compilation;
use rivlet::NodeId;

pub use error::ExecutionError;
pub use outcome::{ExecutionOutcome, ExecutionStatus, NodeFailure, NodeOutcome};

/// Explicit worker capacity and additive initial skips for a single execution.
#[derive(Clone, Debug)]
pub struct ExecutionOptions {
    workers: NonZeroUsize,
    skipped: Vec<NodeId>,
}

impl ExecutionOptions {
    /// Creates options with no initial skips.
    ///
    /// # Errors
    /// Returns an error for zero workers.
    pub fn new(workers: usize) -> Result<Self, ExecutionError> {
        Ok(Self {
            workers: NonZeroUsize::new(workers).ok_or(ExecutionError::InvalidWorkers)?,
            skipped: Vec::new(),
        })
    }

    /// Adds compiled node indexes to skip; never rewrites the graph declaration.
    #[must_use]
    pub fn skip(mut self, nodes: impl IntoIterator<Item = NodeId>) -> Self {
        self.skipped.extend(nodes);
        self
    }
}

/// One single-use execution of an already compiled Rust declaration graph.
pub struct Execution {
    compilation: Arc<Compilation>,
    options: ExecutionOptions,
}

impl Execution {
    #[must_use]
    pub fn new(compilation: impl Into<Arc<Compilation>>, options: ExecutionOptions) -> Self {
        Self {
            compilation: compilation.into(),
            options,
        }
    }

    /// Runs to completion, including independent branches after an action failure.
    ///
    /// Action failures are retained in the returned outcome, not returned as early
    /// errors. Idle execution waits on worker completion, not a timer. Action
    /// contexts use their default (no log path); custom hosts can compose the
    /// low-level session and executor instead.
    ///
    /// # Errors
    /// Returns an error for invalid skip indexes, unsupported predicates, executor
    /// infrastructure failures, or inconsistent scheduler/result transitions.
    pub fn run(self) -> Result<ExecutionOutcome, ExecutionError> {
        driver::run(self.compilation, self.options)
    }
}
