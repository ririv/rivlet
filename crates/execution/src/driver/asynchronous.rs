use super::{
    state::{ActionSite, Mode, State},
    workers::PoolOwner,
};
use crate::{ExecutionError, ExecutionOptions, ExecutionOutcome};
use action::{ActionError, ActionFuture, Value};
use futures_util::{Stream, stream::FuturesUnordered};
use graph::{Compilation, ConditionEvaluation, ConditionFuture};
use rivlet::ConditionId;
use std::future::{Future, poll_fn};
use std::pin::Pin;
use std::sync::Arc;
use std::task::{Context, Poll};

enum Pending {
    Condition {
        id: ConditionId,
        future: ConditionFuture,
    },
    Action {
        site: ActionSite,
        future: ActionFuture,
    },
}
enum Progress {
    Workers,
    Condition(ConditionId, Result<bool, ActionError>),
    Action(ActionSite, Result<Value, ActionError>),
    Cancel,
}
impl Future for Pending {
    type Output = Progress;
    fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Progress> {
        match self.get_mut() {
            Self::Condition { id, future } => future
                .as_mut()
                .poll(cx)
                .map(|value| Progress::Condition(*id, value)),
            Self::Action { site, future } => future
                .as_mut()
                .poll(cx)
                .map(|value| Progress::Action(*site, value)),
        }
    }
}

pub(crate) async fn run_async(
    compilation: Arc<Compilation>,
    options: ExecutionOptions,
) -> Result<ExecutionOutcome, ExecutionError> {
    let mut state = State::new(compilation, options, PoolOwner::Async(None), Mode::Async)?;
    let mut pending = FuturesUnordered::new();
    loop {
        let mut resolutions = Vec::new();
        for (id, evaluation) in state.dispatch()? {
            match evaluation {
                ConditionEvaluation::Ready(result) => resolutions.push(state.resolve(id, result)),
                ConditionEvaluation::Pending(future) => {
                    pending.push(Pending::Condition { id, future });
                }
            }
        }
        for (site, future) in state.take_async_actions() {
            pending.push(Pending::Action { site, future });
        }
        if state.is_finished() {
            let (outcome, workers, observer_error) = state.finish()?;
            if let Some(shutdown) = workers.shutdown() {
                shutdown.await;
            }
            return outcome.delivered(observer_error);
        }
        if state.cancellation.is_cancelled()
            && pending
                .iter()
                .any(|item| matches!(item, Pending::Condition { .. }))
        {
            // 谓词可丢弃；动作必须继续 poll 到真实收尾，不能把 Future drop 冒充协作取消。
            for item in std::mem::take(&mut pending) {
                match item {
                    Pending::Condition { id, .. } => {
                        resolutions.push(state.resolve(id, Err(ActionError::Cancelled)));
                    }
                    action @ Pending::Action { .. } => pending.push(action),
                }
            }
        }
        if !resolutions.is_empty() || state.cancellation_pending() {
            state.advance(resolutions);
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
        let token = state.cancellation.clone();
        let cancelling_at_wait = token.is_cancelled();
        let cancellation = token.cancelled();
        let mut cancellation = std::pin::pin!(cancellation);
        let progress = poll_fn(|cx| {
            if !cancelling_at_wait && cancellation.as_mut().poll(cx).is_ready() {
                return Poll::Ready(Progress::Cancel);
            }
            if let Poll::Ready(Some(progress)) = Pin::new(&mut pending).poll_next(cx) {
                return Poll::Ready(progress);
            }
            if let Some(wait) = &mut action_wait {
                if Pin::new(wait).poll(cx).is_ready() {
                    return Poll::Ready(Progress::Workers);
                }
            }
            Poll::Pending
        })
        .await;
        match progress {
            Progress::Cancel => state.advance(Vec::new()),
            Progress::Workers => {
                state.complete_actions()?;
                state.advance(Vec::new());
            }
            Progress::Condition(id, result) => {
                let resolution = state.resolve(id, result);
                state.advance(vec![resolution]);
            }
            Progress::Action(site, result) => {
                state.complete_async(site, result)?;
                state.advance(Vec::new());
            }
        }
    }
}
