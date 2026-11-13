use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex};

use action::{NativeActionContext, NativeActionExecutor, NativeActionHandle, NativeValue};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use rivlet::{
    ConditionId, ConditionResolution, ExecutionBatch, ExecutionEvent, ExecutionSession,
    ExecutionUpdate, NodeId, NodeState, SessionStatus,
};

use crate::result_values::{NativeResult, ValueCodecs};
use crate::{NativeExecutionBatch, events::EncodedEvent};

const NATIVE_ACTION_STARTED: u8 = 5;
const NATIVE_ACTION_COMPLETED: u8 = 6;
type ActionOutcome = (NodeId, Result<(), ()>);
type NativeFailure = (usize, String);

#[derive(Clone)]
pub(crate) struct NativeActionDeclaration {
    pub(crate) action: NativeActionHandle,
    pub(crate) codecs: Arc<ValueCodecs>,
    pub(crate) inputs: Vec<usize>,
}

#[derive(Clone)]
pub(crate) struct NativeActionBinding {
    declaration: NativeActionDeclaration,
    context: NativeActionContext,
}

/// Rust action ownership and result references aligned with compiled indexes.
#[pyclass(frozen, module = "rivlet._native", skip_from_py_object)]
#[derive(Clone)]
pub(crate) struct NativeActionBindings {
    actions: Vec<Option<NativeActionDeclaration>>,
    python_sources: Vec<usize>,
}

impl NativeActionBindings {
    pub(crate) fn new(
        actions: Vec<Option<NativeActionDeclaration>>,
        python_sources: Vec<usize>,
    ) -> Self {
        Self {
            actions,
            python_sources,
        }
    }
}

#[pymethods]
impl NativeActionBindings {
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
    native_running: usize,
    values: HashMap<usize, NativeValue>,
    python_values: HashMap<usize, Py<PyAny>>,
}

enum NativeDispatch {
    PythonOnly,
    Mixed {
        executor: NativeActionExecutor,
        bindings: Vec<Option<NativeActionBinding>>,
        result_sources: HashSet<usize>,
    },
}

/// Rust-owned session that dispatches Rust actions without a Python callback.
#[pyclass(frozen, module = "rivlet._native")]
pub(crate) struct NativeMixedExecutionSession {
    state: Mutex<MixedExecutionState>,
    actions: NativeDispatch,
    retain_results: bool,
}

#[pyclass(frozen, module = "rivlet._native")]
pub(crate) struct NativeMixedExecutionBatch {
    actions: Vec<usize>,
    conditions: Vec<(usize, usize)>,
    events: Vec<EncodedEvent>,
    finished: bool,
    native_running: usize,
    failures: Vec<NativeFailure>,
    results: Vec<(usize, Py<NativeResult>)>,
}

#[pymethods]
impl NativeMixedExecutionBatch {
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
        Vec<NativeFailure>,
        Vec<(usize, Py<NativeResult>)>,
    ) {
        (
            self.actions.clone(),
            self.conditions.clone(),
            self.events.clone(),
            self.finished,
            self.native_running,
            self.failures.clone(),
            self.results
                .iter()
                .map(|(node, value)| (*node, value.clone_ref(py)))
                .collect(),
        )
    }
}

impl NativeMixedExecutionSession {
    pub(crate) fn new(
        session: ExecutionSession,
        bindings: Vec<Option<NativeActionBinding>>,
        workers: usize,
        retain_results: bool,
    ) -> PyResult<Self> {
        if workers == 0 {
            return Err(PyValueError::new_err(
                "native action workers must be greater than zero",
            ));
        }
        let native_count = bindings.iter().filter(|binding| binding.is_some()).count();
        // 纯 Python session 不启动 Rust worker，也不构造原生值包装。
        let actions = if native_count == 0 {
            NativeDispatch::PythonOnly
        } else {
            let result_sources = bindings
                .iter()
                .flatten()
                .flat_map(|binding| binding.declaration.inputs.iter().copied())
                .collect();
            NativeDispatch::Mixed {
                executor: NativeActionExecutor::new(workers.min(native_count))
                    .map_err(|error| PyValueError::new_err(error.to_string()))?,
                bindings,
                result_sources,
            }
        };
        Ok(Self {
            state: Mutex::new(MixedExecutionState {
                session,
                native_running: 0,
                values: HashMap::new(),
                python_values: HashMap::new(),
            }),
            actions,
            retain_results,
        })
    }

    pub(crate) fn bindings(
        node_count: usize,
        declarations: &NativeActionBindings,
        contexts: Vec<(usize, Option<String>)>,
    ) -> PyResult<Vec<Option<NativeActionBinding>>> {
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
            *slot = Some(NativeActionBinding {
                declaration: declaration.clone(),
                context: NativeActionContext::new(log_path.map(Into::into)),
            });
        }
        Ok(bindings)
    }

    fn inputs(
        py: Python<'_>,
        state: &MixedExecutionState,
        binding: &NativeActionBinding,
    ) -> Result<Vec<Option<NativeValue>>, String> {
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
        mut batch: ExecutionBatch,
    ) -> PyResult<NativeMixedExecutionBatch> {
        let mut result = NativeMixedExecutionBatch {
            actions: Vec::new(),
            conditions: Vec::new(),
            events: Vec::new(),
            finished: false,
            native_running: 0,
            failures: Vec::new(),
            results: Vec::new(),
        };
        loop {
            for event in batch.events() {
                if let (
                    ExecutionEvent::CompositeCompleted(node),
                    NativeDispatch::Mixed { result_sources, .. },
                ) = (event, &self.actions)
                {
                    // 子图完成的结果是 ()；不是用空值掩盖缺失的叶子结果。
                    if result_sources.contains(&node.index()) {
                        state.values.insert(node.index(), NativeValue::new(()));
                    }
                }
                result.events.push(super::encode_event(event));
            }
            let mut failed = Vec::new();
            for node in batch.actions() {
                let binding = match &self.actions {
                    NativeDispatch::PythonOnly => None,
                    NativeDispatch::Mixed {
                        executor, bindings, ..
                    } => bindings[node.index()]
                        .as_ref()
                        .map(|binding| (executor, binding)),
                };
                if let Some((executor, binding)) = binding {
                    result
                        .events
                        .push(EncodedEvent::Lifecycle(NATIVE_ACTION_STARTED, node.index()));
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
                            state.native_running += 1;
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
        result.native_running = state.native_running;
        Ok(result)
    }

    #[allow(clippy::type_complexity)]
    fn drain_native_completions(
        &self,
        py: Python<'_>,
        state: &mut MixedExecutionState,
    ) -> PyResult<(
        Vec<ActionOutcome>,
        Vec<EncodedEvent>,
        Vec<NativeFailure>,
        Vec<(usize, Py<NativeResult>)>,
    )> {
        let NativeDispatch::Mixed {
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
            state.native_running = state
                .native_running
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
                                NativeResult::new(
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
                    events.push(EncodedEvent::Lifecycle(NATIVE_ACTION_COMPLETED, node));
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
impl NativeMixedExecutionSession {
    fn start(&self, py: Python<'_>, capacity: usize) -> PyResult<NativeMixedExecutionBatch> {
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
    ) -> PyResult<NativeMixedExecutionBatch> {
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
        let (native_completed, mut native_events, mut failures, results) =
            self.drain_native_completions(py, &mut state)?;
        let newly_available = native_completed.len();
        let batch = state
            .session
            .advance(
                ExecutionUpdate::new(
                    completed
                        .into_iter()
                        .map(|(node, success)| {
                            (NodeId::new(node), if success { Ok(()) } else { Err(()) })
                        })
                        .chain(native_completed),
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
        native_events.append(&mut result.events);
        result.events = native_events;
        failures.append(&mut result.failures);
        result.failures = failures;
        result.results = results;
        Ok(result)
    }

    /// Waits for actual Rust completion without retaining the Python interpreter.
    fn wait_native(&self, py: Python<'_>) -> PyResult<()> {
        let wait = {
            let state = self
                .state
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            if state.native_running == 0 {
                return Ok(());
            }
            match &self.actions {
                NativeDispatch::PythonOnly => {
                    return Err(PyRuntimeError::new_err(
                        "Python-only session contains native work",
                    ));
                }
                NativeDispatch::Mixed { executor, .. } => executor.prepare_wait(),
            }
        };
        py.detach(move || wait.wait());
        Ok(())
    }
}

impl From<NativeExecutionBatch> for NativeMixedExecutionBatch {
    fn from(batch: NativeExecutionBatch) -> Self {
        Self {
            actions: batch.actions,
            conditions: batch.conditions,
            events: batch.events,
            finished: batch.finished,
            native_running: 0,
            failures: Vec::new(),
            results: Vec::new(),
        }
    }
}
