//! Rust execution hosts composing one shared scheduler, workers and typed results.
//!
//! Graph declarations and the low-level session/executor APIs remain independent.
//! An Execution is consumed by `run` or `run_async`; an `Arc<Compilation>` can be shared by separate
//! executions, each with its own workers and results. No Python runtime is involved.
//!
//! ```
//! use execution::{Execution, ExecutionOptions, ExecutionStatus};
//! use graph::Graph;
//! # fn main() -> Result<(), Box<dyn std::error::Error>> {
//! let compilation = Graph::new("scope", [], [])?.compile()?;
//! let outcome = Execution::new(compilation, ExecutionOptions::new(4)?).run()?;
//! assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
//! # Ok(())
//! # }
//! ```

/// Experimental cross-language coordination; not required by the pure Rust driver.
#[cfg(feature = "experimental-mixed-execution")]
pub mod coordinator;
mod driver;
mod error;
mod outcome;

use std::num::NonZeroUsize;
use std::sync::Arc;

use graph::Compilation;
use rivlet::NodeId;

pub use error::ExecutionError;
pub use outcome::{ConditionOutcome, ExecutionOutcome, ExecutionStatus, NodeFailure, NodeOutcome};

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

    /// Runs synchronous actions alongside synchronous and asynchronous predicates.
    ///
    /// The caller provides the Future executor and any required I/O/timer runtime.
    /// Dropping the Future drops pending predicates; already-submitted synchronous actions
    /// finish under their pool owner. No blocking worker join runs on the caller's thread.
    ///
    /// # Errors
    /// Returns infrastructure or session transition errors; user failures stay in the outcome.
    pub async fn run_async(self) -> Result<ExecutionOutcome, ExecutionError> {
        driver::run_async(self.compilation, self.options).await
    }

    /// Runs synchronous declarations to completion, preserving independent branches.
    ///
    /// Predicates run on this thread outside action worker slots. The action pool is lazy.
    ///
    /// # Errors
    /// Rejects asynchronous predicates before dispatching any action. Also returns
    /// invalid skips, executor failures, or inconsistent scheduler/result transitions.
    pub fn run(self) -> Result<ExecutionOutcome, ExecutionError> {
        driver::run(self.compilation, self.options)
    }
}
