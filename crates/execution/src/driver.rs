mod asynchronous;
mod results;
mod state;
mod workers;

use std::sync::Arc;

use graph::{Compilation, ConditionEvaluation};
use rivlet::{ConditionId, SessionStatus};

use crate::{ExecutionError, ExecutionOptions, ExecutionOutcome};

use state::State;
use workers::PoolOwner;

pub(crate) use asynchronous::run_async;

pub(crate) fn run(
    compilation: Arc<Compilation>,
    options: ExecutionOptions,
) -> Result<ExecutionOutcome, ExecutionError> {
    // 在任何动作开始前拒绝异步声明，不把 Future 阻塞求值或执行到一半再报错。
    for index in 0..compilation.graph().conditions().len() {
        let condition = ConditionId::new(index);
        if compilation.condition(condition).is_async() {
            return Err(ExecutionError::AsyncPredicate(condition));
        }
    }
    let mut state = State::new(compilation, options, PoolOwner::Blocking(None))?;
    let mut batch = state.start()?;
    loop {
        let work = state.dispatch(&batch)?;
        let mut resolutions = Vec::with_capacity(work.len());
        for (id, evaluation) in work {
            match evaluation {
                ConditionEvaluation::Ready(result) => resolutions.push(state.resolve(id, result)),
                ConditionEvaluation::Pending(_) => return Err(ExecutionError::AsyncPredicate(id)),
            }
        }
        if batch.status() != SessionStatus::Running {
            let (outcome, workers) = state.finish()?;
            drop(workers);
            return Ok(outcome);
        }
        if !resolutions.is_empty() {
            batch = state.advance(Vec::new(), resolutions)?;
            continue;
        }
        if !state.actions_running() {
            return Err(ExecutionError::Stalled);
        }
        state
            .workers
            .prepare_wait()
            .expect("running actions own workers")
            .wait();
        let completed = state.complete_actions()?;
        batch = state.advance(completed, Vec::new())?;
    }
}
