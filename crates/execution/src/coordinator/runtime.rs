use std::collections::{HashMap, HashSet};

use action::{ActionError, CompletionWait, Value, ValueType};
use rivlet::{
    ConditionResolution, ExecutionBatch, ExecutionEvent, ExecutionNodeKind, ExecutionSession,
    ExecutionUpdate, NodeId, NodeState, SessionError,
};

use super::{
    ActionDispatch, ConditionBinding, ConditionDispatch, CoordinationBatch, CoordinationError,
    CoordinationEvent, HostValues, InputSite, NativeFailure, Workers, pool::ActionPool,
};

type Completion = (NodeId, Result<(), ()>);

/// One core session plus native workers/results; host objects never enter this layer.
pub struct Coordinator {
    session: ExecutionSession,
    actions: ActionPool,
    conditions: Conditions,
    sources: HashSet<NodeId>,
    values: HashMap<NodeId, Value>,
    retain_results: bool,
}

enum Conditions {
    Host,
    Mixed(Vec<ConditionDispatch>),
}

impl Conditions {
    fn native(&self, id: rivlet::ConditionId) -> Option<&ConditionBinding> {
        match self {
            Self::Host => None,
            Self::Mixed(bindings) => match &bindings[id.index()] {
                ConditionDispatch::Host => None,
                ConditionDispatch::Native(binding) => Some(binding),
            },
        }
    }
}

impl Coordinator {
    /// Connects complete native/host dispatch tables to an existing core session.
    ///
    /// # Errors
    /// Returns binding-table, source-reference or native executor creation errors.
    pub fn new(
        session: ExecutionSession,
        actions: Vec<ActionDispatch>,
        conditions: Vec<ConditionDispatch>,
        workers: Workers,
        retain_results: bool,
    ) -> Result<Self, CoordinationError> {
        validate_bindings(&session, &actions, &conditions)?;
        let sources = actions
            .iter()
            .filter_map(|binding| match binding {
                ActionDispatch::Host => None,
                ActionDispatch::Native(binding) => Some(binding.inputs.iter()),
            })
            .flatten()
            .chain(
                conditions
                    .iter()
                    .filter_map(|binding| match binding {
                        ConditionDispatch::Host => None,
                        ConditionDispatch::Native(binding) => Some(binding.inputs.iter()),
                    })
                    .flatten(),
            )
            .copied()
            .collect();
        let conditions = if conditions
            .iter()
            .any(|condition| matches!(condition, ConditionDispatch::Native(_)))
        {
            Conditions::Mixed(conditions)
        } else {
            Conditions::Host
        };
        Ok(Self {
            session,
            actions: ActionPool::new(actions, workers)?,
            conditions,
            sources,
            values: HashMap::new(),
            retain_results,
        })
    }

    /// Starts scheduling and directly consumes native work from the ready batch.
    ///
    /// # Errors
    /// Returns core transition or native executor infrastructure errors.
    pub fn start(
        &mut self,
        capacity: usize,
        host: &mut impl HostValues,
    ) -> Result<CoordinationBatch, CoordinationError> {
        let batch = self.session.start(capacity)?;
        self.dispatch(batch, capacity, host, CoordinationBatch::new())
    }

    /// Applies host outcomes and drained native completions before further scheduling.
    ///
    /// # Errors
    /// Returns core transition, completion-accounting or executor infrastructure errors.
    pub fn advance(
        &mut self,
        update: ExecutionUpdate,
        available: usize,
        host: &mut impl HostValues,
    ) -> Result<CoordinationBatch, CoordinationError> {
        let mut result = CoordinationBatch::new();
        let completed = self.drain_completions(&mut result)?;
        let available = available
            .checked_add(completed.len())
            .ok_or(CoordinationError::CapacityExceeded)?;
        let batch = self
            .session
            .advance(update.append_completions(completed), available)?;
        self.dispatch(batch, available, host, result)
    }

    /// Releases admission held by a host action while a dynamic child runs.
    ///
    /// # Errors
    /// Returns an invalid node/state transition from the core session.
    pub fn suspend_action(&mut self, node: NodeId) -> Result<Vec<usize>, SessionError> {
        self.session.suspend_action(node)
    }

    #[must_use]
    pub fn prepare_wait(&self) -> Option<CompletionWait> {
        self.actions.prepare_wait()
    }

    /// Whether driving this coordinator may synchronously invoke a native predicate.
    #[must_use]
    pub const fn evaluates_native_conditions(&self) -> bool {
        matches!(self.conditions, Conditions::Mixed(_))
    }

    fn inputs(
        &self,
        sources: &[NodeId],
        types: &[ValueType],
        site: InputSite,
        host: &mut impl HostValues,
    ) -> Result<Vec<Option<Value>>, ActionError> {
        sources
            .iter()
            .zip(types)
            .map(|(source, expected)| {
                let value = match self.session.state(*source) {
                    Some(NodeState::Skipped) => return Ok(None),
                    Some(NodeState::Finished) => match self.values.get(source) {
                        Some(value) => value.clone(),
                        None => host.import(*source, *expected, site)?,
                    },
                    _ => {
                        return Err(ActionError::new(
                            "native result source is not successfully completed or skipped",
                        ));
                    }
                };
                if value.type_id() != expected.id() {
                    return Err(ActionError::new(
                        "host result conversion returned an incompatible native type",
                    ));
                }
                Ok(Some(value))
            })
            .collect()
    }

    fn dispatch(
        &mut self,
        mut batch: ExecutionBatch,
        mut available: usize,
        host: &mut impl HostValues,
        mut result: CoordinationBatch,
    ) -> Result<CoordinationBatch, CoordinationError> {
        loop {
            available = available
                .checked_sub(batch.actions().len())
                .ok_or(CoordinationError::CapacityExceeded)?;
            for event in batch.events() {
                if let ExecutionEvent::CompositeCompleted(node) = event {
                    // 子图完成产生真实 unit；仅在原生消费者需要时保留，不伪造跳过值。
                    if self.sources.contains(node) {
                        self.values.insert(*node, Value::new(()));
                    }
                }
                result.events.push(CoordinationEvent::Scheduler(*event));
            }
            let failed = self.dispatch_actions(batch.actions(), host, &mut result)?;
            let resolved = self.resolve_conditions(&batch, host, &mut result);
            if failed.is_empty() && resolved.is_empty() {
                break;
            }
            available += failed.len();
            batch = self
                .session
                .advance(ExecutionUpdate::new(failed, resolved, []), available)?;
        }
        result.status = batch.status();
        result.native_running = self.actions.running();
        Ok(result)
    }

    fn dispatch_actions(
        &mut self,
        actions: &[NodeId],
        host: &mut impl HostValues,
        result: &mut CoordinationBatch,
    ) -> Result<Vec<Completion>, CoordinationError> {
        let mut failed = vec![];
        for node in actions {
            let Some(binding) = self.actions.binding(*node) else {
                result.actions.push(*node);
                continue;
            };
            result.events.push(CoordinationEvent::ActionStarted(*node));
            match self.inputs(
                &binding.inputs,
                binding.action.input_types(),
                InputSite::Action(*node),
                host,
            ) {
                Ok(values) => self.actions.submit(*node, values)?,
                Err(error) => {
                    failed.push((*node, Err(())));
                    result
                        .failures
                        .push(NativeFailure::Action { node: *node, error });
                }
            }
        }
        Ok(failed)
    }

    fn resolve_conditions(
        &self,
        batch: &ExecutionBatch,
        host: &mut impl HostValues,
        result: &mut CoordinationBatch,
    ) -> Vec<ConditionResolution> {
        let mut resolved = vec![];
        for request in batch.conditions() {
            match self.conditions.native(request.condition()) {
                None => result.conditions.push(*request),
                Some(binding) => {
                    let decision = self
                        .inputs(
                            &binding.inputs,
                            &binding.types,
                            InputSite::Condition(request.condition()),
                            host,
                        )
                        .and_then(|values| binding.evaluate(&values));
                    match decision {
                        Ok(value) => {
                            resolved.push(ConditionResolution::new(request.condition(), value));
                        }
                        Err(error) => {
                            result.failures.push(NativeFailure::Condition {
                                node: request.node(),
                                condition: request.condition(),
                                error,
                            });
                            resolved.push(ConditionResolution::failed(request.condition()));
                        }
                    }
                }
            }
        }
        resolved
    }

    fn drain_completions(
        &mut self,
        batch: &mut CoordinationBatch,
    ) -> Result<Vec<Completion>, CoordinationError> {
        let completed = self.actions.drain()?;
        let mut outcomes = Vec::with_capacity(completed.len());
        for completion in completed {
            let node = NodeId::new(completion.node());
            match completion.into_result() {
                Ok(value) => {
                    // 先保存原生值，再把成功交回 core；Rust 消费方只共享载荷。
                    if self.retain_results {
                        batch.results.push((node, value.clone()));
                    }
                    if self.sources.contains(&node) {
                        self.values.insert(node, value);
                    }
                    outcomes.push((node, Ok(())));
                    batch.events.push(CoordinationEvent::ActionCompleted(node));
                }
                Err(error) => {
                    outcomes.push((node, Err(())));
                    batch.failures.push(NativeFailure::Action { node, error });
                }
            }
        }
        Ok(outcomes)
    }
}

fn validate_bindings(
    session: &ExecutionSession,
    actions: &[ActionDispatch],
    conditions: &[ConditionDispatch],
) -> Result<(), CoordinationError> {
    let invalid = |message| CoordinationError::Binding(ActionError::new(message));
    let graph = session.graph();
    if graph.nodes().len() != actions.len() || graph.conditions().len() != conditions.len() {
        return Err(invalid(
            "native dispatch tables do not match the compiled graph",
        ));
    }
    for (index, binding) in actions.iter().enumerate() {
        if let ActionDispatch::Native(binding) = binding {
            if graph.nodes()[index].kind() != ExecutionNodeKind::Step
                || binding
                    .inputs
                    .iter()
                    .any(|source| source.index() >= actions.len())
            {
                return Err(invalid(
                    "native action binding references an invalid compiled node",
                ));
            }
        }
    }
    for (index, binding) in conditions.iter().enumerate() {
        if let ConditionDispatch::Native(binding) = binding {
            if binding.inputs != graph.conditions()[index].reads() {
                return Err(invalid(
                    "native predicate inputs differ from the compiled condition",
                ));
            }
        }
    }
    Ok(())
}
