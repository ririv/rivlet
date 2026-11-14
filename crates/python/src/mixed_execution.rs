use std::collections::{HashMap, HashSet};
use std::num::NonZeroUsize;
use std::sync::{Arc, Mutex};

use action::{ActionContext, ActionExecutor, ActionHandle, CompletionWait, Value};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use rivlet::{
    ConditionId, ConditionResolution, ExecutionBatch as CoreExecutionBatch, ExecutionEvent,
    ExecutionSession, ExecutionUpdate, NodeId, NodeState, SessionStatus,
};

use crate::result_values::{RustResult, ValueCodecs};
use crate::{ExecutionBatch, events::EncodedEvent};

const RUST_ACTION_STARTED: u8 = 5;
const RUST_ACTION_COMPLETED: u8 = 6;
type ActionOutcome = (NodeId, Result<(), ()>);
type RustFailure = (usize, String);

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

#[derive(Clone)]
pub(crate) struct RustActionDeclaration {
    pub(crate) action: ActionHandle,
    pub(crate) codecs: Arc<ValueCodecs>,
    pub(crate) inputs: Vec<usize>,
}

#[derive(Clone)]
pub(crate) struct RustActionBinding {
    declaration: RustActionDeclaration,
    context: ActionContext,
}

/// Rust action ownership and result references aligned with compiled indexes.
#[pyclass(frozen, module = "rivlet._native", skip_from_py_object)]
#[derive(Clone)]
pub(crate) struct RustActionBindings {
    actions: Vec<Option<RustActionDeclaration>>,
    python_sources: Vec<usize>,
}

impl RustActionBindings {
    pub(crate) fn new(
        actions: Vec<Option<RustActionDeclaration>>,
        python_sources: Vec<usize>,
    ) -> Self {
        Self {
            actions,
            python_sources,
        }
    }
}

#[pymethods]
impl RustActionBindings {
    #[new]
    fn empty(node_count: usize) -> Self {
        Self::new(vec![None; node_count], Vec::new())
    }
    fn python_sources(&self) -> Vec<usize> {
        self.python_sources.clone()
    }
}

struct MixedExecutionState {
    session: ExecutionSession,
    rust_running: usize,
    values: HashMap<usize, Value>,
    python_values: HashMap<usize, Py<PyAny>>,
}

enum ActionDispatch {
    PythonOnly,
    Mixed {
        executor: ActionExecutor,
        bindings: Vec<Option<RustActionBinding>>,
        result_sources: HashSet<usize>,
    },
}

/// Rust-owned session that dispatches Rust actions without a Python callback.
#[pyclass(frozen, module = "rivlet._native")]
pub(crate) struct MixedExecutionSession {
    state: Mutex<MixedExecutionState>,
    actions: ActionDispatch,
    retain_results: bool,
}

#[pyclass(frozen, module = "rivlet._native")]
pub(crate) struct MixedExecutionBatch {
    actions: Vec<usize>,
    conditions: Vec<(usize, usize)>,
    events: Vec<EncodedEvent>,
    finished: bool,
    rust_running: usize,
    failures: Vec<RustFailure>,
    results: Vec<(usize, Py<RustResult>)>,
}

#[pymethods]
impl MixedExecutionBatch {
    #[allow(clippy::type_complexity)]
    fn parts(
        &self,
        py: Python<'_>,
    ) -> (
        Vec<usize>,
        Vec<(usize, usize)>,
        Vec<EncodedEvent>,
        bool,
        usize,
        Vec<RustFailure>,
        Vec<(usize, Py<RustResult>)>,
    ) {
        (
            self.actions.clone(),
            self.conditions.clone(),
            self.events.clone(),
            self.finished,
            self.rust_running,
            self.failures.clone(),
            self.results
                .iter()
                .map(|(node, value)| (*node, value.clone_ref(py)))
                .collect(),
        )
    }
}

impl MixedExecutionSession {
    fn completion_wait(&self) -> PyResult<Option<CompletionWait>> {
        let state = self
            .state
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if state.rust_running == 0 {
            return Ok(None);
        }
        match &self.actions {
            ActionDispatch::PythonOnly => Err(PyRuntimeError::new_err(
                "Python-only session contains native work",
            )),
            ActionDispatch::Mixed { executor, .. } => Ok(Some(executor.prepare_wait())),
        }
    }

    pub(crate) fn new(
        session: ExecutionSession,
        bindings: Vec<Option<RustActionBinding>>,
        workers: usize,
        retain_results: bool,
    ) -> PyResult<Self> {
        if workers == 0 {
            return Err(PyValueError::new_err(
                "native action workers must be greater than zero",
            ));
        }
        let rust_action_count = bindings.iter().filter(|binding| binding.is_some()).count();
        // 纯 Python session 不启动 Rust worker，也不构造原生值包装。
        let actions = if rust_action_count == 0 {
            ActionDispatch::PythonOnly
        } else {
            let result_sources = bindings
                .iter()
                .flatten()
                .flat_map(|binding| binding.declaration.inputs.iter().copied())
                .collect();
            ActionDispatch::Mixed {
                executor: ActionExecutor::new(workers.min(rust_action_count))
                    .map_err(|error| PyValueError::new_err(error.to_string()))?,
                bindings,
                result_sources,
            }
        };
        Ok(Self {
            state: Mutex::new(MixedExecutionState {
                session,
                rust_running: 0,
                values: HashMap::new(),
                python_values: HashMap::new(),
            }),
            actions,
            retain_results,
        })
    }

    pub(crate) fn bindings(
        node_count: usize,
        declarations: &RustActionBindings,
        contexts: Vec<(usize, Option<String>)>,
    ) -> PyResult<Vec<Option<RustActionBinding>>> {
        if declarations.actions.len() != node_count {
            return Err(PyValueError::new_err(
                "native action table does not match execution graph",
            ));
        }
        let mut bindings = vec![None; node_count];
        for (node, log_path) in contexts {
            let Some(slot) = bindings.get_mut(node) else {
                return Err(PyValueError::new_err(
                    "native action references unknown execution node",
                ));
            };
            if slot.is_some() {
                return Err(PyValueError::new_err(
                    "execution node has more than one native action",
                ));
            }
            let Some(declaration) = declarations.actions[node].as_ref() else {
                return Err(PyValueError::new_err(
                    "native action context references Python execution node",
                ));
            };
            *slot = Some(RustActionBinding {
                declaration: declaration.clone(),
                context: ActionContext::new(log_path.map(Into::into)),
            });
        }
        Ok(bindings)
    }

    fn inputs(
        py: Python<'_>,
        state: &MixedExecutionState,
        binding: &RustActionBinding,
    ) -> Result<Vec<Option<Value>>, String> {
        // O(I) 个槽位查询；Rust 值只克隆 Arc。跨语言转换的载荷成本由 codec 决定。
        binding
            .declaration
            .inputs
            .iter()
            .zip(binding.declaration.action.input_types())
            .map(
                |(source, expected)| match state.session.state(NodeId::new(*source)) {
                    Some(NodeState::Skipped) => Ok(None),
                    Some(NodeState::Finished) => {
                        if let Some(value) = state.values.get(source) {
                            return Ok(Some(value.clone()));
                        }
                        let value = state
                            .python_values
                            .get(source)
                            .ok_or_else(|| "completed source has no result value".to_owned())?;
                        binding
                            .declaration
                            .codecs
                            .import(*expected, value.bind(py))
                            .map(Some)
                            .map_err(|error| error.to_string())
                    }
                    _ => Err(
                        "native result source is not successfully completed or skipped".to_owned(),
                    ),
                },
            )
            .collect()
    }

    fn dispatch(
        &self,
        py: Python<'_>,
        state: &mut MixedExecutionState,
        mut batch: CoreExecutionBatch,
    ) -> PyResult<MixedExecutionBatch> {
        let mut result = MixedExecutionBatch {
            actions: Vec::new(),
            conditions: Vec::new(),
            events: Vec::new(),
            finished: false,
            rust_running: 0,
            failures: Vec::new(),
            results: Vec::new(),
        };
        loop {
            for event in batch.events() {
                if let (
                    ExecutionEvent::CompositeCompleted(node),
                    ActionDispatch::Mixed { result_sources, .. },
                ) = (event, &self.actions)
                {
                    // 子图完成的结果是 ()；不是用空值掩盖缺失的叶子结果。
                    if result_sources.contains(&node.index()) {
                        state.values.insert(node.index(), Value::new(()));
                    }
                }
                result.events.push(super::encode_event(event));
            }
            let mut failed = Vec::new();
            for node in batch.actions() {
                let binding = match &self.actions {
                    ActionDispatch::PythonOnly => None,
                    ActionDispatch::Mixed {
                        executor, bindings, ..
                    } => bindings[node.index()]
                        .as_ref()
                        .map(|binding| (executor, binding)),
                };
                if let Some((executor, binding)) = binding {
                    result
                        .events
                        .push(EncodedEvent::Lifecycle(RUST_ACTION_STARTED, node.index()));
                    match Self::inputs(py, state, binding) {
                        Ok(inputs) => {
                            executor
                                .submit(
                                    node.index(),
                                    binding.declaration.action.clone(),
                                    binding.context.clone(),
                                    inputs,
                                )
                                .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
                            state.rust_running += 1;
                        }
                        Err(error) => {
                            // 转换错误属于接收节点失败，不取消无关分支。
                            failed.push((*node, Err(())));
                            result.failures.push((node.index(), error));
                        }
                    }
                } else {
                    result.actions.push(node.index());
                }
            }
            result.conditions.extend(
                batch
                    .conditions()
                    .iter()
                    .map(|request| (request.node().index(), request.condition().index())),
            );
            if failed.is_empty() {
                break;
            }
            let available = failed.len();
            batch = state
                .session
                .advance(ExecutionUpdate::new(failed, [], []), available)
                .map_err(|error| PyValueError::new_err(error.to_string()))?;
        }
        result.finished = batch.status() != SessionStatus::Running;
        result.rust_running = state.rust_running;
        Ok(result)
    }

    #[allow(clippy::type_complexity)]
    fn drain_rust_completions(
        &self,
        py: Python<'_>,
        state: &mut MixedExecutionState,
    ) -> PyResult<(
        Vec<ActionOutcome>,
        Vec<EncodedEvent>,
        Vec<RustFailure>,
        Vec<(usize, Py<RustResult>)>,
    )> {
        let ActionDispatch::Mixed {
            executor,
            bindings,
            result_sources,
        } = &self.actions
        else {
            return Ok((Vec::new(), Vec::new(), Vec::new(), Vec::new()));
        };
        let completions = executor.drain_completions();
        // O(B) 处理本批完成，Rust-only 不创建 Python 包装，也不扫描整张图。
        let mut completed = Vec::with_capacity(completions.len());
        let mut events = Vec::with_capacity(completions.len());
        let mut failures = Vec::new();
        let mut exports = Vec::new();
        for completion in completions {
            state.rust_running = state
                .rust_running
                .checked_sub(1)
                .expect("native completion count exceeds submitted action count");
            let node = completion.node();
            match completion.into_result() {
                Ok(value) => {
                    // 先保存值，再 advance；Rust→Rust 只克隆 Arc，不经过 Python。
                    if self.retain_results {
                        let binding = bindings[node]
                            .as_ref()
                            .expect("completion belongs to a native action");
                        exports.push((
                            node,
                            Py::new(
                                py,
                                RustResult::new(
                                    value.clone(),
                                    Arc::clone(&binding.declaration.codecs),
                                ),
                            )?,
                        ));
                    }
                    if result_sources.contains(&node) {
                        state.values.insert(node, value);
                    }
                    completed.push((NodeId::new(node), Ok(())));
                    events.push(EncodedEvent::Lifecycle(RUST_ACTION_COMPLETED, node));
                }
                Err(error) => {
                    completed.push((NodeId::new(node), Err(())));
                    failures.push((node, error.to_string()));
                }
            }
        }
        Ok((completed, events, failures, exports))
    }
}

#[pymethods]
impl MixedExecutionSession {
    fn suspend_action(&self, node: usize) -> PyResult<Vec<usize>> {
        self.state
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .session
            .suspend_action(NodeId::new(node))
            .map_err(|error| PyValueError::new_err(error.to_string()))
    }

    fn start(&self, py: Python<'_>, capacity: usize) -> PyResult<MixedExecutionBatch> {
        let mut state = self
            .state
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let batch = state
            .session
            .start(capacity)
            .map_err(|error| PyValueError::new_err(error.to_string()))?;
        self.dispatch(py, &mut state, batch)
    }

    #[pyo3(signature = (completed, conditions, skipped, available, values=Vec::new()))]
    fn advance(
        &self,
        py: Python<'_>,
        completed: Vec<(usize, bool)>,
        conditions: Vec<(usize, Option<bool>)>,
        skipped: Vec<usize>,
        available: usize,
        values: Vec<(usize, Py<PyAny>)>,
    ) -> PyResult<MixedExecutionBatch> {
        let mut state = self
            .state
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let successes: HashSet<_> = completed
            .iter()
            .filter_map(|(node, success)| success.then_some(*node))
            .collect();
        for (node, value) in values {
            if !successes.contains(&node) || state.python_values.contains_key(&node) {
                return Err(PyValueError::new_err(
                    "Python result import must accompany one new successful completion",
                ));
            }
            state.python_values.insert(node, value);
        }
        let (rust_completed, mut rust_events, mut failures, results) =
            self.drain_rust_completions(py, &mut state)?;
        let newly_available = rust_completed.len();
        let batch = state
            .session
            .advance(
                ExecutionUpdate::new(
                    completed
                        .into_iter()
                        .map(|(node, success)| {
                            (NodeId::new(node), if success { Ok(()) } else { Err(()) })
                        })
                        .chain(rust_completed),
                    conditions
                        .into_iter()
                        .map(|(condition, value)| match value {
                            Some(value) => {
                                ConditionResolution::new(ConditionId::new(condition), value)
                            }
                            None => ConditionResolution::failed(ConditionId::new(condition)),
                        }),
                    skipped.into_iter().map(NodeId::new),
                ),
                available + newly_available,
            )
            .map_err(|error| PyValueError::new_err(error.to_string()))?;
        let mut result = self.dispatch(py, &mut state, batch)?;
        rust_events.append(&mut result.events);
        result.events = rust_events;
        failures.append(&mut result.failures);
        result.failures = failures;
        result.results = results;
        Ok(result)
    }

    /// Waits for actual Rust completion without retaining the Python interpreter.
    fn wait_rust(&self, py: Python<'_>) -> PyResult<()> {
        if let Some(wait) = self.completion_wait()? {
            py.detach(move || wait.wait());
        }
        Ok(())
    }

    /// Delivers one completion notification without borrowing a Python action worker.
    fn notify_rust(&self, py: Python<'_>, callback: Py<PyAny>) -> PyResult<()> {
        let Some(wait) = self.completion_wait()? else {
            callback.call0(py)?;
            return Ok(());
        };
        // 这里等待的是批次完成信号，不执行 action。不能占用调用方的 Python
        // 动作线程，否则满池可能阻止依赖 Rust 结果的协程继续运行。
        std::thread::Builder::new()
            .name("GraphCompletion".to_owned())
            .spawn(move || {
                wait.wait();
                Python::attach(|py| {
                    if let Err(error) = callback.call0(py) {
                        error.write_unraisable(py, Some(callback.bind(py)));
                    }
                });
            })
            .map_err(|error| {
                PyRuntimeError::new_err(format!("failed to start completion notification: {error}"))
            })?;
        Ok(())
    }
}

impl From<ExecutionBatch> for MixedExecutionBatch {
    fn from(batch: ExecutionBatch) -> Self {
        Self {
            actions: batch.actions,
            conditions: batch.conditions,
            events: batch.events,
            finished: batch.finished,
            rust_running: 0,
            failures: Vec::new(),
            results: Vec::new(),
        }
    }
}
