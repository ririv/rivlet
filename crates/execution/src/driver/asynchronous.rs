use std::future::{Future, poll_fn};
use std::pin::Pin;
use std::sync::Arc;
use std::task::{Context, Poll};

use action::ActionError;
use futures_util::{Stream, stream::FuturesUnordered};
use graph::{Compilation, ConditionEvaluation, ConditionFuture};
use rivlet::{ConditionId, SessionStatus};

use super::{state::State, workers::PoolOwner};
use crate::{ExecutionError, ExecutionOptions, ExecutionOutcome};

struct PendingCondition {
    id: ConditionId,
    future: ConditionFuture,
}

impl Future for PendingCondition {
    type Output = (ConditionId, Result<bool, ActionError>);

    fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Self::Output> {
        let this = self.get_mut();
        this.future
            .as_mut()
            .poll(cx)
            .map(|result| (this.id, result))
    }
}

enum Progress {
    Actions,
    Condition(ConditionId, Result<bool, ActionError>),
}

pub(crate) async fn run_async(
    compilation: Arc<Compilation>,
    options: ExecutionOptions,
) -> Result<ExecutionOutcome, ExecutionError> {
    let mut state = State::new(compilation, options, PoolOwner::Async(None))?;
    let mut pending = FuturesUnordered::new();
    let mut batch = state.start()?;
    loop {
        let mut resolutions = Vec::new();
        for (id, evaluation) in state.dispatch(&batch)? {
            match evaluation {
                ConditionEvaluation::Ready(result) => resolutions.push(state.resolve(id, result)),
                ConditionEvaluation::Pending(future) => {
                    pending.push(PendingCondition { id, future });
                }
            }
        }
        if batch.status() != SessionStatus::Running {
            let (outcome, workers) = state.finish()?;
            if let Some(shutdown) = workers.shutdown() {
                shutdown.await;
            }
            return Ok(outcome);
        }
        if !resolutions.is_empty() {
            batch = state.advance(Vec::new(), resolutions)?;
            continue;
        }
        if !state.actions_running() && pending.is_empty() {
            return Err(ExecutionError::Stalled);
        }
        let mut action_wait = if state.actions_running() {
            state.workers.prepare_wait()
        } else {
            None
        };
        // 同时注册动作与谓词的唤醒；不为等待创建线程、不在事件循环上 Condvar::wait。
        let progress = poll_fn(|cx| {
            // 先推进谓词，避免持续就绪的动作通知让它们一直没有首次 poll。
            if let Poll::Ready(Some((id, result))) = Pin::new(&mut pending).poll_next(cx) {
                return Poll::Ready(Progress::Condition(id, result));
            }
            if let Some(wait) = &mut action_wait {
                if Pin::new(wait).poll(cx).is_ready() {
                    return Poll::Ready(Progress::Actions);
                }
            }
            Poll::Pending
        })
        .await;
        batch = match progress {
            Progress::Actions => {
                let completed = state.complete_actions()?;
                state.advance(completed, Vec::new())?
            }
            Progress::Condition(id, result) => {
                let resolution = state.resolve(id, result);
                state.advance(Vec::new(), vec![resolution])?
            }
        };
    }
}
