//! Ordered, opt-in observation independent of scheduler and user result storage.

use crate::NodeFailure;
use action::{ActionError, CancellationSource, CancellationToken};
use graph::ProjectedNode;
use rivlet::{BlockCause, NodeIdentity};
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicU64, Ordering},
};

#[derive(Clone, Debug)]
pub enum NodeEventDetail {
    Started,
    Completed,
    Skipped,
    Cancelled,
    Failed(NodeFailure),
    Blocked(BlockCause),
}

#[derive(Clone, Debug)]
pub struct NodeExecutionEvent {
    pub sequence: u64,
    pub node_id: NodeIdentity,
    pub node_path: Vec<usize>,
    pub state_detail: NodeEventDetail,
}

pub type NodeEventSink = Arc<dyn Fn(&NodeExecutionEvent) -> Result<(), ActionError> + Send + Sync>;

pub(crate) struct Observations {
    sink: NodeEventSink,
    sequence: AtomicU64,
    stop: CancellationSource,
    error: Mutex<Option<ActionError>>,
}

impl Observations {
    pub(crate) fn new(sink: NodeEventSink) -> Arc<Self> {
        Arc::new(Self {
            sink,
            sequence: AtomicU64::new(0),
            stop: CancellationSource::new(),
            error: Mutex::new(None),
        })
    }

    pub(crate) fn token(&self) -> CancellationToken {
        self.stop.token()
    }
    pub(crate) fn cancel(&self) {
        self.stop.cancel();
    }

    pub(crate) fn emit(
        &self,
        node: &ProjectedNode,
        prefix: &[usize],
        state_detail: NodeEventDetail,
    ) {
        if self
            .error
            .lock()
            .expect("observer error lock poisoned")
            .is_some()
        {
            return;
        }
        let sequence = self
            .sequence
            .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |value| {
                value.checked_add(1)
            })
            .expect("event sequence overflow");
        let mut node_path = prefix.to_vec();
        node_path.extend(node.path());
        let event = NodeExecutionEvent {
            sequence,
            node_id: node.identity(),
            node_path,
            state_detail,
        };
        let result = catch_unwind(AssertUnwindSafe(|| (self.sink)(&event)))
            .unwrap_or_else(|_| Err(ActionError::new("execution observer panicked")));
        if let Err(error) = result {
            *self.error.lock().expect("observer error lock poisoned") = Some(error);
            self.stop.cancel();
        }
    }

    pub(crate) fn error(&self) -> Option<ActionError> {
        self.error
            .lock()
            .expect("observer error lock poisoned")
            .clone()
    }
}
