use std::sync::Arc;

use crate::action::{ActionContext, ActionExecutor, Value};
use crate::graph::{Compilation, ProjectedKind};
use rivlet_core::{
    ConditionRequest, ConditionResolution, ExecutionEvent, ExecutionSession, ExecutionUpdate,
    FailureCause, NodeId, SessionStatus,
};

use super::{
    ConditionOutcome, ExecutionError, ExecutionOptions, ExecutionOutcome, NodeFailure, NodeOutcome,
};

struct Results(Vec<Option<NodeOutcome>>);

impl Results {
    fn new(count: usize) -> Self {
        Self((0..count).map(|_| None).collect())
    }

    fn record(&mut self, node: NodeId, outcome: NodeOutcome) -> Result<(), ExecutionError> {
        let slot = &mut self.0[node.index()];
        if slot.is_some() {
            return Err(ExecutionError::DuplicateOutcome(node));
        }
        *slot = Some(outcome);
        Ok(())
    }

    fn input(&self, source: NodeId) -> Result<Option<Value>, ExecutionError> {
        match &self.0[source.index()] {
            Some(NodeOutcome::Succeeded(value)) => Ok(Some(value.clone())),
            Some(NodeOutcome::Skipped) => Ok(None),
            Some(NodeOutcome::Failed(_) | NodeOutcome::Blocked(_)) => {
                Err(ExecutionError::InvalidTransition(source))
            }
            None => Err(ExecutionError::MissingResult(source)),
        }
    }

    fn events(&mut self, events: &[ExecutionEvent]) -> Result<(), ExecutionError> {
        for event in events {
            match *event {
                ExecutionEvent::CompositeEntered(_)
                | ExecutionEvent::ConditionEvaluated { .. }
                | ExecutionEvent::ConditionSkipped(_) => {}
                ExecutionEvent::CompositeCompleted(node) => {
                    self.record(node, NodeOutcome::Succeeded(Value::new(())))?;
                }
                ExecutionEvent::NodeSkipped(node) => self.record(node, NodeOutcome::Skipped)?,
                ExecutionEvent::NodeBlocked { node, cause } => {
                    self.record(node, NodeOutcome::Blocked(cause))?;
                }
                ExecutionEvent::NodeFailed {
                    node,
                    cause: FailureCause::Child(child),
                } => self.record(node, NodeOutcome::Failed(NodeFailure::Child(child)))?,
                ExecutionEvent::NodeFailed {
                    node,
                    cause: FailureCause::Action,
                } => {
                    if !matches!(
                        self.0[node.index()],
                        Some(NodeOutcome::Failed(NodeFailure::Action(_)))
                    ) {
                        return Err(ExecutionError::InvalidTransition(node));
                    }
                }
            }
        }
        Ok(())
    }

    fn finish(self) -> Result<Vec<NodeOutcome>, ExecutionError> {
        self.0
            .into_iter()
            .enumerate()
            .map(|(index, outcome)| {
                outcome.ok_or(ExecutionError::MissingResult(NodeId::new(index)))
            })
            .collect()
    }
}

fn evaluate_conditions(
    compilation: &Compilation,
    results: &Results,
    outcomes: &mut [ConditionOutcome],
    requests: &[ConditionRequest],
) -> Result<Vec<ConditionResolution>, ExecutionError> {
    let mut resolutions = Vec::with_capacity(requests.len());
    for request in requests {
        let condition = request.condition();
        let predicate = compilation.condition(condition);
        let inputs = predicate
            .inputs()
            .iter()
            .map(|source| results.input(*source))
            .collect::<Result<Vec<_>, _>>()?;
        // 谓词在宿主线程求值，不是动作，也不占 action worker 名额。
        let (outcome, resolution) = match predicate.evaluate(&inputs) {
            Ok(value) => (
                ConditionOutcome::Resolved(value),
                ConditionResolution::new(condition, value),
            ),
            Err(error) => (
                ConditionOutcome::Failed(error),
                ConditionResolution::failed(condition),
            ),
        };
        outcomes[condition.index()] = outcome;
        resolutions.push(resolution);
    }
    Ok(resolutions)
}

pub(crate) fn run(
    compilation: Arc<Compilation>,
    options: ExecutionOptions,
) -> Result<ExecutionOutcome, ExecutionError> {
    let mut session =
        ExecutionSession::new(Arc::new(compilation.graph().clone()), options.skipped)?;
    let workers = options.workers.get();
    let mut results = Results::new(compilation.graph().nodes().len());
    let mut conditions: Vec<_> = (0..compilation.graph().conditions().len())
        .map(|_| ConditionOutcome::NotEvaluated)
        .collect();
    let mut batch = session.start(workers)?;
    // 空图、全跳过图不需要创建空闲线程池。
    if batch.status() != SessionStatus::Running {
        results.events(batch.events())?;
        return Ok(ExecutionOutcome::new(
            compilation,
            results.finish()?,
            conditions,
        ));
    }
    let mut executor = None;
    let mut running: usize = 0;
    loop {
        results.events(batch.events())?;
        if !batch.actions().is_empty() {
            // 只有真要运行动作才建池；仅判断后全部跳过的图不创建空闲 worker。
            if executor.is_none() {
                executor = Some(ActionExecutor::new(workers).map_err(ExecutionError::Executor)?);
            }
        }
        for node in batch.actions() {
            let projected = compilation.node(*node);
            let ProjectedKind::Step(action) = projected.kind() else {
                return Err(ExecutionError::UnexpectedComposite(*node));
            };
            let inputs = projected
                .inputs()
                .iter()
                .map(|source| results.input(*source))
                .collect::<Result<Vec<_>, _>>()?;
            executor
                .as_ref()
                .expect("dispatched actions own an executor")
                .submit(
                    node.index(),
                    action.clone(),
                    ActionContext::default(),
                    inputs,
                )
                .map_err(ExecutionError::Executor)?;
            running += 1;
        }
        if batch.status() != SessionStatus::Running {
            return Ok(ExecutionOutcome::new(
                compilation,
                results.finish()?,
                conditions,
            ));
        }
        if !batch.conditions().is_empty() {
            let resolutions =
                evaluate_conditions(&compilation, &results, &mut conditions, batch.conditions())?;
            batch =
                session.advance(ExecutionUpdate::new([], resolutions, []), workers - running)?;
            continue;
        }
        if running == 0 {
            return Err(ExecutionError::Stalled);
        }
        let executor = executor.as_ref().expect("running actions own an executor");
        executor.wait_for_completion();
        let completions = executor.drain_completions();
        running = running
            .checked_sub(completions.len())
            .ok_or(ExecutionError::InvalidCompletion)?;
        let mut completed = Vec::with_capacity(completions.len());
        for completion in completions {
            let node = NodeId::new(completion.node());
            // 必须先保存值或原始 action 错误，再推进调度状态；完成通知不是结果本身。
            let (outcome, status) = match completion.into_result() {
                Ok(value) => (NodeOutcome::Succeeded(value), Ok(())),
                Err(error) => (NodeOutcome::Failed(NodeFailure::Action(error)), Err(())),
            };
            results.record(node, outcome)?;
            completed.push((node, status));
        }
        // 初始索引和结果表为 O(V)，每个结果槽/事件处理一次，输入绑定为 O(R)。
        // workers 是整个执行的上限，只补充实际空出的槽，不给每批重新发满额度。
        batch = session.advance(ExecutionUpdate::new(completed, [], []), workers - running)?;
    }
}
