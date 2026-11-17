use std::sync::Arc;

use action::{ActionContext, ActionError};
use graph::{Compilation, ConditionEvaluation, ProjectedKind};
use rivlet::{
    ConditionId, ConditionResolution, ExecutionBatch, ExecutionSession, ExecutionUpdate, NodeId,
};

use crate::{
    ConditionOutcome, ExecutionError, ExecutionOptions, ExecutionOutcome, NodeFailure, NodeOutcome,
};

use super::{results::Results, workers::PoolOwner};

pub(super) type Completions = Vec<(NodeId, Result<(), ()>)>;
pub(super) type ConditionWork = (ConditionId, ConditionEvaluation);

/// Shared execution transitions; waiting policy belongs to the two thin entry adapters.
pub(super) struct State {
    compilation: Arc<Compilation>,
    session: ExecutionSession,
    results: Results,
    conditions: Vec<ConditionOutcome>,
    pub(super) workers: PoolOwner,
    limit: usize,
    running: usize,
}

impl State {
    pub(super) fn new(
        compilation: Arc<Compilation>,
        options: ExecutionOptions,
        workers: PoolOwner,
    ) -> Result<Self, ExecutionError> {
        let session =
            ExecutionSession::new(Arc::new(compilation.graph().clone()), options.skipped)?;
        let results = Results::new(compilation.graph().nodes().len());
        let conditions = (0..compilation.graph().conditions().len())
            .map(|_| ConditionOutcome::NotEvaluated)
            .collect();
        Ok(Self {
            compilation,
            session,
            results,
            conditions,
            workers,
            limit: options.workers.get(),
            running: 0,
        })
    }

    pub(super) fn start(&mut self) -> Result<ExecutionBatch, ExecutionError> {
        Ok(self.session.start(self.limit)?)
    }

    pub(super) fn dispatch(
        &mut self,
        batch: &ExecutionBatch,
    ) -> Result<Vec<ConditionWork>, ExecutionError> {
        self.results.events(batch.events())?;
        for node in batch.actions() {
            let projected = self.compilation.node(*node);
            let ProjectedKind::Step(action) = projected.kind() else {
                return Err(ExecutionError::UnexpectedComposite(*node));
            };
            let inputs = projected
                .inputs()
                .iter()
                .map(|source| self.results.input(*source))
                .collect::<Result<Vec<_>, _>>()?;
            self.workers
                .executor(self.limit)
                .map_err(ExecutionError::Executor)?
                .submit(
                    node.index(),
                    action.clone(),
                    ActionContext::default(),
                    inputs,
                )
                .map_err(ExecutionError::Executor)?;
            self.running += 1;
        }
        batch
            .conditions()
            .iter()
            .map(|request| {
                let id = request.condition();
                let predicate = self.compilation.condition(id);
                let inputs = predicate
                    .inputs()
                    .iter()
                    .map(|source| self.results.input(*source))
                    .collect::<Result<Vec<_>, _>>()?;
                Ok((id, predicate.start(&inputs)))
            })
            .collect()
    }

    pub(super) fn resolve(
        &mut self,
        id: ConditionId,
        result: Result<bool, ActionError>,
    ) -> ConditionResolution {
        let (outcome, resolution) = match result {
            Ok(value) => (
                ConditionOutcome::Resolved(value),
                ConditionResolution::new(id, value),
            ),
            Err(error) => (
                ConditionOutcome::Failed(error),
                ConditionResolution::failed(id),
            ),
        };
        self.conditions[id.index()] = outcome;
        resolution
    }

    pub(super) const fn actions_running(&self) -> bool {
        self.running > 0
    }

    pub(super) fn complete_actions(&mut self) -> Result<Completions, ExecutionError> {
        let completions = self.workers.drain();
        self.running = self
            .running
            .checked_sub(completions.len())
            .ok_or(ExecutionError::InvalidCompletion)?;
        completions
            .into_iter()
            .map(|completion| {
                let node = NodeId::new(completion.node());
                let (outcome, status) = match completion.into_result() {
                    Ok(value) => (NodeOutcome::Succeeded(value), Ok(())),
                    Err(error) => (NodeOutcome::Failed(NodeFailure::Action(error)), Err(())),
                };
                // 先提交真实结果，再把完成事实交给唯一的 core 状态机。
                self.results.record(node, outcome)?;
                Ok((node, status))
            })
            .collect()
    }

    pub(super) fn advance(
        &mut self,
        completed: Completions,
        conditions: Vec<ConditionResolution>,
    ) -> Result<ExecutionBatch, ExecutionError> {
        Ok(self.session.advance(
            ExecutionUpdate::new(completed, conditions, []),
            self.limit - self.running,
        )?)
    }

    pub(super) fn finish(self) -> Result<(ExecutionOutcome, PoolOwner), ExecutionError> {
        Ok((
            ExecutionOutcome::new(self.compilation, self.results.finish()?, self.conditions),
            self.workers,
        ))
    }
}
