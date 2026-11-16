//! Experimental Python/Rust mixed execution; ordinary Python sessions bypass this module.

use std::collections::{HashMap, HashSet};
use std::num::NonZeroUsize;
use std::sync::{Arc, Mutex};

use crate::native_structure::NativePredicate;
use action::{ActionContext, ActionHandle, CompletionWait, ValueType};
use execution::coordinator as coordination;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use rivlet::{
    ConditionId, ConditionResolution, ExecutionSession, ExecutionUpdate, NodeId, SessionStatus,
};

use crate::result_values::{RustResult, ValueCodecs};
use crate::{ExecutionBatch, events::EncodedEvent};

const RUST_ACTION_STARTED: u8 = 5;
const RUST_ACTION_COMPLETED: u8 = 6;
type RustFailure = (usize, String);

#[derive(Clone)]
pub(crate) struct RustActionDeclaration {
    pub(crate) action: ActionHandle,
    pub(crate) codecs: Arc<ValueCodecs>,
    pub(crate) inputs: Vec<usize>,
}

#[derive(Clone)]
pub(crate) struct RustConditionDeclaration {
    pub(crate) condition: NativePredicate,
    pub(crate) codecs: Arc<ValueCodecs>,
    pub(crate) inputs: Vec<usize>,
}

/// A structural marker; native predicates are never invoked as Python callables.
#[pyclass(frozen, module = "rivlet._native")]
pub(crate) struct _NativePredicate;

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
    pub(crate) conditions: Vec<Option<RustConditionDeclaration>>,
}

impl RustActionBindings {
    pub(crate) fn has_native(&self) -> bool {
        self.actions.iter().any(Option::is_some) || self.conditions.iter().any(Option::is_some)
    }

    pub(crate) fn validate_host_only(
        &self,
        node_count: usize,
        contexts: &[(usize, Option<String>)],
        workers: Option<usize>,
    ) -> PyResult<()> {
        if self.actions.len() != node_count || !contexts.is_empty() {
            return Err(PyValueError::new_err(
                "host-only dispatch table does not match execution graph",
            ));
        }
        if workers == Some(0) {
            return Err(PyValueError::new_err(
                "native action workers must be greater than zero",
            ));
        }
        Ok(())
    }

    pub(crate) fn new(
        actions: Vec<Option<RustActionDeclaration>>,
        python_sources: Vec<usize>,
        conditions: Vec<Option<RustConditionDeclaration>>,
    ) -> Self {
        Self {
            actions,
            python_sources,
            conditions,
        }
    }
}

#[pymethods]
impl RustActionBindings {
    #[new]
    fn empty(node_count: usize) -> Self {
        Self::new(vec![None; node_count], Vec::new(), Vec::new())
    }
    fn python_sources(&self) -> Vec<usize> {
        self.python_sources.clone()
    }
}

struct HostCodecs {
    actions: Vec<Option<Arc<ValueCodecs>>>,
    conditions: Vec<Option<Arc<ValueCodecs>>>,
}

impl HostCodecs {
    fn require(
        &self,
        site: coordination::InputSite,
    ) -> Result<&Arc<ValueCodecs>, action::ActionError> {
        let codec = match site {
            coordination::InputSite::Action(node) => self.actions.get(node.index()),
            coordination::InputSite::Condition(condition) => self.conditions.get(condition.index()),
        };
        codec
            .and_then(Option::as_ref)
            .ok_or_else(|| action::ActionError::new("native receiver has no owning host codec"))
    }
}

struct PythonValues<'a> {
    values: &'a HashMap<usize, Py<PyAny>>,
    codecs: &'a HostCodecs,
}

impl coordination::HostValues for PythonValues<'_> {
    fn import(
        &mut self,
        source: NodeId,
        expected: ValueType,
        site: coordination::InputSite,
    ) -> Result<action::Value, action::ActionError> {
        let value = self.values.get(&source.index()).ok_or_else(|| {
            action::ActionError::new("completed source has no Python result value")
        })?;
        let codec = self.codecs.require(site)?;
        Python::attach(|py| codec.import(expected, value.bind(py)))
            .map_err(|error| action::ActionError::new(error.to_string()))
    }
}

struct MixedExecutionState {
    coordinator: coordination::Coordinator,
    python_values: HashMap<usize, Py<PyAny>>,
}

/// Thin Python adaptation of the language-independent native coordinator.
#[pyclass(frozen, module = "rivlet._native")]
pub(crate) struct MixedExecutionSession {
    state: Mutex<MixedExecutionState>,
    codecs: HostCodecs,
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

fn coordination_error(error: &coordination::CoordinationError) -> PyErr {
    match error {
        coordination::CoordinationError::Session(_)
        | coordination::CoordinationError::Binding(_) => PyValueError::new_err(error.to_string()),
        _ => PyRuntimeError::new_err(error.to_string()),
    }
}

impl MixedExecutionSession {
    fn completion_wait(&self) -> Option<CompletionWait> {
        self.state
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .coordinator
            .prepare_wait()
    }

    pub(crate) fn new(
        session: ExecutionSession,
        bindings: Vec<Option<RustActionBinding>>,
        workers: Option<usize>,
        retain_results: bool,
        conditions: Vec<Option<RustConditionDeclaration>>,
    ) -> PyResult<Self> {
        let workers = match workers {
            None => coordination::Workers::Automatic,
            Some(value) => {
                coordination::Workers::Fixed(NonZeroUsize::new(value).ok_or_else(|| {
                    PyValueError::new_err("native action workers must be greater than zero")
                })?)
            }
        };
        let (actions, action_codecs) = native_actions(bindings)?;
        let (conditions, condition_codecs) =
            native_conditions(session.graph().conditions().len(), conditions)?;
        let coordinator =
            coordination::Coordinator::new(session, actions, conditions, workers, retain_results)
                .map_err(|error| coordination_error(&error))?;
        Ok(Self {
            state: Mutex::new(MixedExecutionState {
                coordinator,
                python_values: HashMap::new(),
            }),
            codecs: HostCodecs {
                actions: action_codecs,
                conditions: condition_codecs,
            },
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

    fn encode_batch(
        &self,
        py: Python<'_>,
        batch: coordination::CoordinationBatch,
    ) -> PyResult<MixedExecutionBatch> {
        use coordination::CoordinationEvent;
        let events = batch
            .events
            .into_iter()
            .map(|event| match event {
                CoordinationEvent::Scheduler(event) => super::encode_event(&event),
                CoordinationEvent::ActionStarted(node) => {
                    EncodedEvent::Lifecycle(RUST_ACTION_STARTED, node.index())
                }
                CoordinationEvent::ActionCompleted(node) => {
                    EncodedEvent::Lifecycle(RUST_ACTION_COMPLETED, node.index())
                }
            })
            .collect();
        let results = batch
            .results
            .into_iter()
            .map(|(node, value)| {
                let codec = self
                    .codecs
                    .require(coordination::InputSite::Action(node))
                    .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
                Ok((
                    node.index(),
                    Py::new(py, RustResult::new(value, Arc::clone(codec)))?,
                ))
            })
            .collect::<PyResult<_>>()?;
        Ok(MixedExecutionBatch {
            actions: batch.actions.into_iter().map(NodeId::index).collect(),
            conditions: batch
                .conditions
                .into_iter()
                .map(|request| (request.node().index(), request.condition().index()))
                .collect(),
            events,
            finished: batch.status != SessionStatus::Running,
            rust_running: batch.native_running,
            failures: batch
                .failures
                .into_iter()
                .map(|failure| match failure {
                    coordination::NativeFailure::Action { node, error }
                    | coordination::NativeFailure::Condition { node, error, .. } => {
                        (node.index(), error.to_string())
                    }
                })
                .collect(),
            results,
        })
    }
}

type NativeActions = (
    Vec<coordination::ActionDispatch>,
    Vec<Option<Arc<ValueCodecs>>>,
);
type NativeConditions = (
    Vec<coordination::ConditionDispatch>,
    Vec<Option<Arc<ValueCodecs>>>,
);

fn native_actions(bindings: Vec<Option<RustActionBinding>>) -> PyResult<NativeActions> {
    let mut actions = Vec::with_capacity(bindings.len());
    let mut codecs = Vec::with_capacity(bindings.len());
    for binding in bindings {
        match binding {
            None => {
                actions.push(coordination::ActionDispatch::Host);
                codecs.push(None);
            }
            Some(binding) => {
                let action = coordination::ActionBinding::new(
                    binding.declaration.action,
                    binding.context,
                    binding
                        .declaration
                        .inputs
                        .into_iter()
                        .map(NodeId::new)
                        .collect(),
                )
                .map_err(|error| PyValueError::new_err(error.to_string()))?;
                actions.push(coordination::ActionDispatch::Native(action));
                codecs.push(Some(binding.declaration.codecs));
            }
        }
    }
    Ok((actions, codecs))
}

fn native_conditions(
    count: usize,
    bindings: Vec<Option<RustConditionDeclaration>>,
) -> PyResult<NativeConditions> {
    // 私有 empty bindings 明确表示纯宿主条件；非空表必须与编译侧表完整对齐。
    if !bindings.is_empty() && bindings.len() != count {
        return Err(PyValueError::new_err(
            "native predicate table does not match execution graph",
        ));
    }
    let mut conditions = vec![coordination::ConditionDispatch::Host; count];
    let mut codecs = vec![None; count];
    for (index, declaration) in bindings.into_iter().enumerate() {
        if let Some(declaration) = declaration {
            let predicate = declaration.condition;
            let condition = coordination::ConditionBinding::new(
                predicate.input_types().to_vec(),
                declaration.inputs.into_iter().map(NodeId::new).collect(),
                move |values| predicate.evaluate(values),
            )
            .map_err(|error| PyValueError::new_err(error.to_string()))?;
            conditions[index] = coordination::ConditionDispatch::Native(condition);
            codecs[index] = Some(declaration.codecs);
        }
    }
    Ok((conditions, codecs))
}

#[pymethods]
impl MixedExecutionSession {
    fn suspend_action(&self, node: usize) -> PyResult<Vec<usize>> {
        self.state
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .coordinator
            .suspend_action(NodeId::new(node))
            .map_err(|error| PyValueError::new_err(error.to_string()))
    }

    fn start(&self, py: Python<'_>, capacity: usize) -> PyResult<MixedExecutionBatch> {
        let mut state = self
            .state
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let MixedExecutionState {
            coordinator,
            python_values,
        } = &mut *state;
        let mut values = PythonValues {
            values: python_values,
            codecs: &self.codecs,
        };
        // 仅原生谓词可能在当前线程执行用户 Rust 代码；纯宿主路径不承担释放/重取 GIL。
        let batch = if coordinator.evaluates_native_conditions() {
            py.detach(|| coordinator.start(capacity, &mut values))
        } else {
            coordinator.start(capacity, &mut values)
        }
        .map_err(|error| coordination_error(&error))?;
        self.encode_batch(py, batch)
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
        let update = ExecutionUpdate::new(
            completed
                .into_iter()
                .map(|(node, success)| (NodeId::new(node), if success { Ok(()) } else { Err(()) })),
            conditions
                .into_iter()
                .map(|(condition, value)| match value {
                    Some(value) => ConditionResolution::new(ConditionId::new(condition), value),
                    None => ConditionResolution::failed(ConditionId::new(condition)),
                }),
            skipped.into_iter().map(NodeId::new),
        );
        let MixedExecutionState {
            coordinator,
            python_values,
        } = &mut *state;
        let mut values = PythonValues {
            values: python_values,
            codecs: &self.codecs,
        };
        let batch = if coordinator.evaluates_native_conditions() {
            py.detach(|| coordinator.advance(update, available, &mut values))
        } else {
            coordinator.advance(update, available, &mut values)
        }
        .map_err(|error| coordination_error(&error))?;
        self.encode_batch(py, batch)
    }

    /// Waits for actual Rust completion without retaining the Python interpreter.
    fn wait_rust(&self, py: Python<'_>) {
        if let Some(wait) = self.completion_wait() {
            py.detach(move || wait.wait());
        }
    }

    /// Delivers one completion notification without borrowing a Python action worker.
    fn notify_rust(&self, py: Python<'_>, callback: Py<PyAny>) -> PyResult<()> {
        let Some(wait) = self.completion_wait() else {
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
