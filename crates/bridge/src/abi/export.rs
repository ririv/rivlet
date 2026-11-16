use std::mem::ManuallyDrop;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::path::PathBuf;
use std::sync::Arc;

use action::{ActionContext, ActionError, Value};
use graph::{GraphStructure, NodeKind, ProjectedKind, Projection};

use super::values::{error, export_value, inputs, provider};
use super::{ABI_VERSION, Bytes, GraphFunctions, Header, Input, Outcome, Payload};
use crate::Snapshot;
use std::ffi::c_void;
use std::mem::size_of;

struct ExportState {
    projection: Projection,
    description: Vec<u8>,
}
unsafe extern "C" fn graph_describe(context: *const c_void) -> Bytes {
    Bytes::of(&unsafe { &*context.cast::<ExportState>() }.description)
}
unsafe extern "C" fn graph_release(context: *const c_void) {
    drop(unsafe { Arc::from_raw(context.cast::<ExportState>()) });
}
unsafe extern "C" fn graph_execute(
    context: *const c_void,
    index: usize,
    pointer: *const Input,
    count: usize,
    log_present: u32,
    log: Bytes,
) -> Outcome {
    let operation = || -> Result<Value, ActionError> {
        let state = unsafe { &*context.cast::<ExportState>() };
        let node = state
            .projection
            .nodes()
            .get(index)
            .ok_or_else(|| ActionError::new("native action index is outside its graph"))?;
        let ProjectedKind::Step(action) = node.kind() else {
            return Err(ActionError::new("native graph is not an action"));
        };
        let log = match log_present {
            0 => None,
            1 => Some(PathBuf::from(
                std::str::from_utf8(unsafe { log.slice() })
                    .map_err(|error| ActionError::new(error.to_string()))?,
            )),
            _ => return Err(ActionError::new("invalid native log path tag")),
        };
        action.execute(&ActionContext::new(log), &unsafe {
            inputs(pointer, count)?
        })
    };
    match catch_unwind(AssertUnwindSafe(operation)) {
        Ok(Ok(result)) => match export_value(result) {
            Ok(value) => Outcome {
                tag: 0,
                payload: Payload { value },
            },
            Err(message) => error(&message),
        },
        Ok(Err(message)) => error(&message),
        Err(_) => error("native action panicked"),
    }
}
unsafe extern "C" fn graph_evaluate(
    context: *const c_void,
    index: usize,
    pointer: *const Input,
    count: usize,
) -> Outcome {
    let operation = || -> Result<bool, ActionError> {
        let state = unsafe { &*context.cast::<ExportState>() };
        let condition = state
            .projection
            .conditions()
            .get(index)
            .ok_or_else(|| ActionError::new("native predicate index is outside its graph"))?;
        condition.evaluate(&unsafe { inputs(pointer, count)? })
    };
    match catch_unwind(AssertUnwindSafe(operation)) {
        Ok(Ok(decision)) => Outcome {
            tag: 2,
            payload: Payload {
                decision: u8::from(decision),
            },
        },
        Ok(Err(message)) => error(&message),
        Err(_) => error("native predicate panicked"),
    }
}
unsafe extern "C" fn graph_project(context: *const c_void, scope: usize) -> Outcome {
    let operation = || -> Result<ExportedGraph, ActionError> {
        let state = unsafe { &*context.cast::<ExportState>() };
        let scope = state
            .projection
            .scopes()
            .get(scope)
            .ok_or_else(|| ActionError::new("native scope index is outside its graph"))?;
        ExportedGraph::new(Arc::clone(scope.structure()))
    };
    match catch_unwind(AssertUnwindSafe(operation)) {
        Ok(Ok(graph)) => Outcome {
            tag: 4,
            payload: Payload {
                graph: ManuallyDrop::new(graph).0,
            },
        },
        Ok(Err(message)) => error(&message),
        Err(_) => error("native graph projection panicked"),
    }
}
static GRAPH_FUNCTIONS: GraphFunctions = GraphFunctions {
    size: size_of::<GraphFunctions>(),
    provider,
    describe: graph_describe,
    execute: graph_execute,
    evaluate: graph_evaluate,
    project: graph_project,
    release: graph_release,
};

/// A capsule payload containing only a C-layout header and an origin-owned lease.
#[repr(transparent)]
pub struct ExportedGraph(Header);

// ExportState 只持有 Send+Sync 的 Action/Condition；ABI 指针仅指向此不可变 Arc。
unsafe impl Send for ExportedGraph {}

impl ExportedGraph {
    /// Exports immutable declaration metadata and origin-owned callbacks.
    ///
    /// # Errors
    /// Returns structural/type declaration or metadata encoding errors.
    pub fn new(structure: Arc<GraphStructure>) -> Result<Self, ActionError> {
        let projection = Projection::from_structure(structure)
            .map_err(|error| ActionError::new(error.to_string()))?;
        Self::from_projection(projection)
    }

    /// Exports one concrete node, including its root metadata and predicate.
    ///
    /// # Errors
    /// Returns structural/type declaration or metadata encoding errors.
    pub fn from_node(node: NodeKind) -> Result<Self, ActionError> {
        let projection =
            Projection::from_node(node).map_err(|error| ActionError::new(error.to_string()))?;
        Self::from_projection(projection)
    }

    fn from_projection(projection: Projection) -> Result<Self, ActionError> {
        let snapshot = Snapshot::from_projection(&projection)?;
        let description =
            serde_json::to_vec(&snapshot).map_err(|error| ActionError::new(error.to_string()))?;
        Ok(Self(Header {
            version: ABI_VERSION,
            size: size_of::<Header>(),
            context: Arc::into_raw(Arc::new(ExportState {
                projection,
                description,
            }))
            .cast(),
            functions: &raw const GRAPH_FUNCTIONS,
        }))
    }
}
impl Drop for ExportedGraph {
    fn drop(&mut self) {
        unsafe {
            ((*self.0.functions).release)(self.0.context);
        }
    }
}
