//! Node-API adapter for the existing language-independent graph compiler/session.

mod events;
mod schema;

use std::num::NonZeroUsize;
use std::sync::Arc;

use napi::bindgen_prelude::{BigInt, Error, Result, Status};
use napi_derive::napi;
use rivlet::{
    CompiledExecutionGraph, ConcurrencyLimits, ConditionId, ConditionResolution, ExecutionSession,
    ExecutionUpdate, GraphDeclaration, NodeId, NodeState,
};
use serde::Deserialize;
use serde_json::{Value, json};

fn invalid(error: impl std::fmt::Display) -> Error {
    Error::new(Status::InvalidArg, error.to_string())
}

#[derive(Deserialize)]
#[serde(tag = "kind", rename_all = "camelCase", deny_unknown_fields)]
enum Completion {
    Succeeded { node: usize },
    Failed { node: usize },
}

#[derive(Deserialize)]
#[serde(tag = "kind", rename_all = "camelCase", deny_unknown_fields)]
enum Condition {
    Resolved { condition: usize, value: bool },
    Failed { condition: usize },
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Update {
    completed: Vec<Completion>,
    conditions: Vec<Condition>,
    skipped: Vec<usize>,
}

#[derive(Deserialize)]
#[serde(tag = "kind", rename_all = "camelCase", deny_unknown_fields)]
enum Admission {
    Unbounded,
    Limited {
        maxima: Vec<NonZeroUsize>,
        memberships: Vec<Vec<usize>>,
    },
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SessionOptions {
    skipped: Vec<usize>,
    admission: Admission,
}

/// Immutable core compilation; no JS callback or result value enters Rust.
#[napi]
pub struct CompiledGraph {
    graph: Arc<CompiledExecutionGraph>,
    nodes: Vec<usize>,
    conditions: Vec<usize>,
}

#[napi]
impl CompiledGraph {
    #[must_use]
    #[napi(ts_return_type = "unknown")]
    pub fn mappings(&self) -> Value {
        json!({"nodes":self.nodes, "conditions":self.conditions})
    }

    /// Creates independent state and admission counters for one execution.
    ///
    /// # Errors
    /// Returns an error for invalid skipped nodes or concurrency memberships.
    #[napi(ts_args_type = "options: unknown")]
    pub fn session(&self, options: Value) -> Result<Session> {
        let options: SessionOptions = serde_json::from_value(options).map_err(invalid)?;
        let skipped = options
            .skipped
            .into_iter()
            .map(NodeId::new)
            .collect::<Vec<_>>();
        let session = match options.admission {
            Admission::Unbounded => ExecutionSession::new(Arc::clone(&self.graph), skipped),
            Admission::Limited {
                maxima,
                memberships,
            } => {
                let concurrency = ConcurrencyLimits::new(maxima)
                    .bind(memberships)
                    .map_err(invalid)?;
                ExecutionSession::new(Arc::clone(&self.graph), skipped)
                    .and_then(|session| session.with_concurrency(concurrency))
            }
        }
        .map_err(invalid)?;
        Ok(Session {
            session,
            node_count: self.graph.nodes().len(),
        })
    }
}

/// One mutable core session, driven in batches by the JS event loop.
#[napi]
pub struct Session {
    session: ExecutionSession,
    node_count: usize,
}

#[napi]
impl Session {
    /// Starts the session and returns its first scheduling batch.
    ///
    /// # Errors
    /// Returns an error if the session has already started or capacity is zero.
    #[napi(ts_return_type = "unknown")]
    pub fn start(&mut self, capacity: u32) -> Result<Value> {
        self.session
            .start(capacity as usize)
            .map(|value| events::batch(&value))
            .map_err(invalid)
    }

    /// Applies completed actions and predicates, then schedules another batch.
    ///
    /// # Errors
    /// Returns an error for malformed updates or invalid session transitions.
    #[napi(
        ts_args_type = "update: unknown, capacity: number",
        ts_return_type = "unknown"
    )]
    pub fn advance(&mut self, update: Value, capacity: u32) -> Result<Value> {
        let update: Update = serde_json::from_value(update).map_err(invalid)?;
        let completed = update.completed.into_iter().map(|item| match item {
            Completion::Succeeded { node } => (NodeId::new(node), Ok(())),
            Completion::Failed { node } => (NodeId::new(node), Err(())),
        });
        let conditions = update.conditions.into_iter().map(|item| match item {
            Condition::Resolved { condition, value } => {
                ConditionResolution::new(ConditionId::new(condition), value)
            }
            Condition::Failed { condition } => {
                ConditionResolution::failed(ConditionId::new(condition))
            }
        });
        self.session
            .advance(
                ExecutionUpdate::new(
                    completed,
                    conditions,
                    update.skipped.into_iter().map(NodeId::new),
                ),
                capacity as usize,
            )
            .map(|value| events::batch(&value))
            .map_err(invalid)
    }

    /// Returns states in compiled node order.
    ///
    /// # Errors
    /// Returns an error if the core session does not contain a compiled node.
    #[napi]
    pub fn states(&self) -> Result<Vec<String>> {
        (0..self.node_count)
            .map(|index| {
                let state = self
                    .session
                    .state(NodeId::new(index))
                    .ok_or_else(|| invalid("unknown compiled node"))?;
                Ok(match state {
                    NodeState::Pending => "pending",
                    NodeState::Ready => "ready",
                    NodeState::Running => "running",
                    NodeState::Active => "active",
                    NodeState::Finished => "succeeded",
                    NodeState::Skipped => "skipped",
                    NodeState::Failed => "failed",
                    NodeState::Blocked => "blocked",
                }
                .to_owned())
            })
            .collect()
    }
}

#[must_use]
#[napi]
pub fn allocate_node_id() -> BigInt {
    rivlet::NodeIdentity::allocate().value().into()
}

#[must_use]
#[napi]
pub fn package_version() -> String {
    env!("CARGO_PKG_VERSION").to_owned()
}

/// Compiles opaque declarations with the same compiler used by Python and Rust.
///
/// # Errors
/// Returns an error for malformed declarations or invalid graph topology.
#[napi(ts_args_type = "input: unknown")]
pub fn compile_declaration(input: Value) -> Result<CompiledGraph> {
    let declaration: schema::Declaration = serde_json::from_value(input).map_err(invalid)?;
    let compilation = GraphDeclaration::from(declaration)
        .compile()
        .map_err(invalid)?;
    let nodes = compilation
        .declaration_nodes()
        .iter()
        .map(|node| node.index())
        .collect();
    let conditions = compilation
        .declaration_conditions()
        .iter()
        .map(|condition| condition.index())
        .collect();
    Ok(CompiledGraph {
        graph: Arc::new(compilation.into_graph()),
        nodes,
        conditions,
    })
}
