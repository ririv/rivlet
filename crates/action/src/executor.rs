use std::collections::VecDeque;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::{Arc, Condvar, Mutex, mpsc};
use std::thread::{self, JoinHandle};

use crate::{ActionContext, ActionError, ActionHandle, Value};

struct ActionJob {
    node: usize,
    action: ActionHandle,
    context: ActionContext,
    inputs: Vec<Option<Value>>,
}

/// One native action completion retained until its graph session consumes it.
#[derive(Debug)]
pub struct ActionCompletion {
    node: usize,
    result: Result<Value, ActionError>,
}

impl ActionCompletion {
    #[must_use]
    pub const fn node(&self) -> usize {
        self.node
    }

    /// Returns the concrete action result.
    ///
    /// # Errors
    /// Returns the error produced by the native action.
    pub fn into_result(self) -> Result<Value, ActionError> {
        self.result
    }
}

#[derive(Default)]
struct CompletionState {
    values: VecDeque<ActionCompletion>,
    revision: u64,
}

#[derive(Default)]
struct CompletionQueue {
    state: Mutex<CompletionState>,
    ready: Condvar,
}

enum WaitState {
    Ready,
    Pending(u64),
}

/// A wait bound to the completion queue and revision observed at preparation.
pub struct CompletionWait {
    queue: Arc<CompletionQueue>,
    state: WaitState,
}

impl CompletionWait {
    pub fn wait(self) {
        match self.state {
            WaitState::Ready => (),
            WaitState::Pending(revision) => {
                let mut state = self
                    .queue
                    .state
                    .lock()
                    .unwrap_or_else(std::sync::PoisonError::into_inner);
                // 即使另一个 driver 已取走完成记录，序号变化也保留这次通知。
                while state.values.is_empty() && state.revision == revision {
                    state = self
                        .queue
                        .ready
                        .wait(state)
                        .unwrap_or_else(std::sync::PoisonError::into_inner);
                }
            }
        }
    }
}

struct ExecutorInner {
    jobs: mpsc::Sender<Option<ActionJob>>,
    completions: Arc<CompletionQueue>,
    workers: Vec<JoinHandle<()>>,
}

impl Drop for ExecutorInner {
    fn drop(&mut self) {
        for _ in &self.workers {
            let _ = self.jobs.send(None);
        }
        for worker in self.workers.drain(..) {
            let _ = worker.join();
        }
    }
}

/// A bounded Rust worker set used only for Rust-owned actions.
#[derive(Clone)]
pub struct ActionExecutor {
    inner: Arc<ExecutorInner>,
}

impl ActionExecutor {
    /// Creates a native worker set with an explicit positive capacity.
    ///
    /// # Errors
    /// Returns an error when capacity is zero or an operating-system thread
    /// cannot be created.
    pub fn new(workers: usize) -> Result<Self, ActionError> {
        if workers == 0 {
            return Err(ActionError::new(
                "native action workers must be greater than zero",
            ));
        }
        let (jobs, receiver) = mpsc::channel::<Option<ActionJob>>();
        let receiver = Arc::new(Mutex::new(receiver));
        let completions = Arc::new(CompletionQueue::default());
        let mut worker_handles = Vec::with_capacity(workers);
        for index in 0..workers {
            let receiver = Arc::clone(&receiver);
            let completions = Arc::clone(&completions);
            let worker = thread::Builder::new()
                .name(format!("ActionWorker-{index}"))
                .spawn(move || worker_loop(&receiver, &completions))
                .map_err(|error| {
                    ActionError::new(format!("failed to create native graph worker: {error}"))
                })?;
            worker_handles.push(worker);
        }
        Ok(Self {
            inner: Arc::new(ExecutorInner {
                jobs,
                completions,
                workers: worker_handles,
            }),
        })
    }

    /// Submits one action to the native worker set.
    ///
    /// # Errors
    /// Returns an error when the executor has already stopped accepting work.
    pub fn submit(
        &self,
        node: usize,
        action: ActionHandle,
        context: ActionContext,
        inputs: Vec<Option<Value>>,
    ) -> Result<(), ActionError> {
        self.inner
            .jobs
            .send(Some(ActionJob {
                node,
                action,
                context,
                inputs,
            }))
            .map_err(|_| ActionError::new("native action executor is closed"))
    }

    /// Blocks until at least one completion can be drained.
    pub fn wait_for_completion(&self) {
        self.prepare_wait().wait();
    }

    #[must_use]
    pub fn prepare_wait(&self) -> CompletionWait {
        let state = self
            .inner
            .completions
            .state
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        CompletionWait {
            queue: Arc::clone(&self.inner.completions),
            state: if state.values.is_empty() {
                WaitState::Pending(state.revision)
            } else {
                WaitState::Ready
            },
        }
    }

    #[must_use]
    pub fn drain_completions(&self) -> Vec<ActionCompletion> {
        self.inner
            .completions
            .state
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .values
            .drain(..)
            .collect()
    }
}

fn worker_loop(jobs: &Mutex<mpsc::Receiver<Option<ActionJob>>>, completions: &CompletionQueue) {
    loop {
        let job = jobs
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .recv();
        let Ok(Some(job)) = job else {
            return;
        };
        let result = catch_unwind(AssertUnwindSafe(|| {
            job.action.execute(&job.context, &job.inputs)
        }))
        .unwrap_or_else(|_| Err(ActionError::new("native action panicked")));
        let mut state = completions
            .state
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        state.values.push_back(ActionCompletion {
            node: job.node,
            result,
        });
        state.revision = state
            .revision
            .checked_add(1)
            .expect("native completion revision overflow");
        drop(state);
        completions.ready.notify_all();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::Action;
    use std::sync::mpsc;
    use std::time::Duration;

    struct Noop;

    impl Action for Noop {
        type Inputs = ();
        type Output = ();
        fn execute(&self, _context: &ActionContext, (): ()) -> Result<(), ActionError> {
            Ok(())
        }
    }

    #[test]
    fn prepared_wait_survives_completion_drain() {
        let executor = ActionExecutor::new(1).unwrap();
        let pending = executor.prepare_wait();
        executor
            .submit(
                0,
                ActionHandle::new(Noop),
                ActionContext::default(),
                Vec::new(),
            )
            .unwrap();
        executor.wait_for_completion();
        let ready = executor.prepare_wait();
        assert_eq!(executor.drain_completions().len(), 1);
        let (sent, received) = mpsc::channel();
        let waiter = thread::spawn(move || {
            pending.wait();
            ready.wait();
            sent.send(()).unwrap();
        });
        received
            .recv_timeout(Duration::from_secs(2))
            .expect("completion notification was lost after drain");
        waiter.join().unwrap();
    }
}
