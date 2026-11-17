mod asynchronous;
mod results;
mod state;
mod workers;

use std::sync::Arc;

use graph::{Compilation, ConditionEvaluation, ProjectedKind};
use rivlet::{ConditionId, NodeId};

use crate::{ExecutionError, ExecutionOptions, ExecutionOutcome};

use state::{Mode, State};
use workers::PoolOwner;

pub(crate) use asynchronous::run_async;

pub(crate) fn run(
    compilation: Arc<Compilation>,
    options: ExecutionOptions,
) -> Result<ExecutionOutcome, ExecutionError> {
    check_sync(&compilation)?;
    let mut state = State::new(compilation, options, PoolOwner::Blocking(None), Mode::Sync)?;
    loop {
        let work = state.dispatch()?;
        let mut resolutions = Vec::with_capacity(work.len());
        for (id, evaluation) in work {
            match evaluation {
                ConditionEvaluation::Ready(result) => resolutions.push(state.resolve(id, result)),
                ConditionEvaluation::Pending(_) => return Err(ExecutionError::AsyncPredicate(id)),
            }
        }
        if state.is_finished() {
            let (outcome, workers, observer_error) = state.finish()?;
            drop(workers);
            return outcome.delivered(observer_error);
        }
        if !resolutions.is_empty() || state.cancellation_pending() {
            state.advance(resolutions);
            continue;
        }
        if !state.actions_running() {
            return Err(ExecutionError::Stalled);
        }
        let wait = state
            .workers
            .prepare_wait()
            .expect("running actions own workers");
        let _wake = (!state.cancellation.is_cancelled())
            .then(|| wait.wake_on_cancellation(&state.cancellation));
        wait.wait();
        state.complete_actions()?;
        state.advance(Vec::new());
    }
}

fn check_sync(compilation: &Compilation) -> Result<(), ExecutionError> {
    // 在任何动作开始前拒绝异步声明，不把 Future 阻塞求值或执行到一半再报错。
    for index in 0..compilation.graph().nodes().len() {
        let node = NodeId::new(index);
        if matches!(compilation.node(node).kind(),ProjectedKind::Step(action) if action.is_async())
        {
            return Err(ExecutionError::AsyncAction(node));
        }
    }
    for index in 0..compilation.graph().conditions().len() {
        let condition = ConditionId::new(index);
        if compilation.condition(condition).is_async() {
            return Err(ExecutionError::AsyncPredicate(condition));
        }
    }
    Ok(())
}
