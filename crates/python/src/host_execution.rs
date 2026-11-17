//! Ordinary Python execution uses only the core session, not native coordination.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use rivlet::{ExecutionSession as CoreSession, NodeId};
use std::num::NonZeroUsize;

use crate::{ExecutionBatch, ExecutionSession, events::EncodedEvent};

/// Shared admission counters owned by one Python execution driver.
#[pyclass(frozen, module = "rivlet._native", name = "_ConcurrencyLimits")]
pub(crate) struct ConcurrencyLimits {
    pub(crate) limits: rivlet::ConcurrencyLimits,
}

#[pymethods]
impl ConcurrencyLimits {
    #[new]
    fn new(maxima: Vec<usize>) -> PyResult<Self> {
        let maxima = maxima
            .into_iter()
            .map(|maximum| {
                NonZeroUsize::new(maximum)
                    .ok_or_else(|| PyValueError::new_err("concurrency limits must be positive"))
            })
            .collect::<PyResult<Vec<_>>>()?;
        Ok(Self {
            limits: rivlet::ConcurrencyLimits::new(maxima),
        })
    }

    fn revision(&self) -> u64 {
        self.limits.revision()
    }
}

/// The common driver protocol, with no native work or foreign values.
#[pyclass(frozen, module = "rivlet._native")]
pub(crate) struct HostExecutionBatch(ExecutionBatch);

#[pymethods]
impl HostExecutionBatch {
    #[allow(clippy::type_complexity)]
    fn parts(
        &self,
    ) -> (
        Vec<usize>,
        Vec<(usize, usize)>,
        Vec<EncodedEvent>,
        bool,
        usize,
        Vec<(usize, String)>,
        Vec<(usize, Py<PyAny>)>,
    ) {
        (
            self.0.actions.clone(),
            self.0.conditions.clone(),
            self.0.events.clone(),
            self.0.finished,
            0,
            vec![],
            vec![],
        )
    }
}

/// A core-only session for Python actions, predicates and dynamic subgraphs.
#[pyclass(module = "rivlet._native")]
pub(crate) struct HostExecutionSession {
    session: ExecutionSession,
}

impl HostExecutionSession {
    pub(crate) fn new(session: CoreSession) -> Self {
        Self {
            session: ExecutionSession { session },
        }
    }
}

#[pymethods]
impl HostExecutionSession {
    fn request_cancel(&mut self) {
        self.session.request_cancel();
    }
    fn start(&mut self, capacity: usize) -> PyResult<HostExecutionBatch> {
        self.session.start(capacity).map(HostExecutionBatch)
    }

    #[pyo3(signature = (completed, conditions, skipped, available, values=Vec::new(), cancelled=Vec::new(), cancelled_conditions=Vec::new()))]
    // 固定批量 FFI 协议；不把 Python 容器或取消语义下沉进执行器。
    #[allow(clippy::too_many_arguments)]
    fn advance(
        &mut self,
        completed: Vec<(usize, bool)>,
        conditions: Vec<(usize, Option<bool>)>,
        skipped: Vec<usize>,
        available: usize,
        values: Vec<(usize, Py<PyAny>)>,
        cancelled: Vec<usize>,
        cancelled_conditions: Vec<usize>,
    ) -> PyResult<HostExecutionBatch> {
        if values.into_iter().next().is_some() {
            return Err(PyValueError::new_err(
                "host-only execution does not accept native result imports",
            ));
        }
        self.session
            .advance(
                completed,
                conditions,
                skipped,
                available,
                cancelled,
                cancelled_conditions,
            )
            .map(HostExecutionBatch)
    }

    fn suspend_action(&mut self, node: usize) -> PyResult<Vec<usize>> {
        self.session
            .session
            .suspend_action(NodeId::new(node))
            .map_err(|error| PyValueError::new_err(error.to_string()))
    }

    // 统一 driver 协议：没有原生在途工作，等待已完成，通知立即就绪。
    #[staticmethod]
    fn wait_rust() {}

    #[staticmethod]
    fn notify_rust(callback: &Bound<'_, PyAny>) -> PyResult<()> {
        callback.call0()?;
        Ok(())
    }
}
