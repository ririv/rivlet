use std::future::Future;
use std::pin::Pin;
use std::sync::{Arc, Mutex, mpsc};
use std::task::{Context, Poll, Waker};
use std::thread;

use action::{ActionCompletion, ActionError, ActionExecutor, CompletionWait};

/// Ownership policy, not a second scheduler or another action budget.
pub(super) enum PoolOwner {
    Blocking(Option<ActionExecutor>),
    Async(Option<AsyncPool>),
}

impl PoolOwner {
    pub(super) fn executor(&mut self, capacity: usize) -> Result<&ActionExecutor, ActionError> {
        match self {
            Self::Blocking(pool) => {
                if pool.is_none() {
                    *pool = Some(ActionExecutor::new(capacity)?);
                }
                Ok(pool.as_ref().expect("initialized blocking pool"))
            }
            Self::Async(pool) => {
                if pool.is_none() {
                    *pool = Some(AsyncPool::new(capacity)?);
                }
                Ok(&pool.as_ref().expect("initialized async owner").executor)
            }
        }
    }

    pub(super) fn prepare_wait(&self) -> Option<CompletionWait> {
        match self {
            Self::Blocking(pool) => pool.as_ref().map(ActionExecutor::prepare_wait),
            Self::Async(pool) => pool.as_ref().map(|pool| pool.executor.prepare_wait()),
        }
    }

    pub(super) fn drain(&self) -> Vec<ActionCompletion> {
        match self {
            Self::Blocking(pool) => pool
                .as_ref()
                .map_or_else(Vec::new, ActionExecutor::drain_completions),
            Self::Async(pool) => pool
                .as_ref()
                .map_or_else(Vec::new, |pool| pool.executor.drain_completions()),
        }
    }

    pub(super) fn shutdown(self) -> Option<Shutdown> {
        match self {
            Self::Blocking(_) | Self::Async(None) => None,
            Self::Async(Some(pool)) => Some(pool.shutdown()),
        }
    }
}

enum ShutdownState {
    Waiting(Option<Waker>),
    Complete,
}

pub(super) struct AsyncPool {
    // 字段按声明顺序释放：先放下本线程的 executor 引用，再关闭 release 通道。
    // 清理线程始终持有最后一个引用，因此取消 Future 不会在事件循环内 join。
    executor: ActionExecutor,
    _release: mpsc::Sender<Infallible>,
    shutdown: Arc<Mutex<ShutdownState>>,
}

impl AsyncPool {
    fn new(capacity: usize) -> Result<Self, ActionError> {
        let executor = ActionExecutor::new(capacity)?;
        let owner = executor.clone();
        let (release, stopped) = mpsc::channel();
        let shutdown = Arc::new(Mutex::new(ShutdownState::Waiting(None)));
        let signal = Arc::clone(&shutdown);
        thread::Builder::new()
            .name("ActionCleanup".into())
            .spawn(move || {
                // 通道仅用关闭信号；没有逐动作消息，也不创建每谓词线程。
                let _ = stopped.recv();
                drop(owner);
                let previous = {
                    let mut state = signal
                        .lock()
                        .unwrap_or_else(std::sync::PoisonError::into_inner);
                    std::mem::replace(&mut *state, ShutdownState::Complete)
                };
                if let ShutdownState::Waiting(Some(waker)) = previous {
                    waker.wake();
                }
            })
            .map_err(|error| {
                ActionError::new(format!("failed to create action cleanup thread: {error}"))
            })?;
        Ok(Self {
            executor,
            _release: release,
            shutdown,
        })
    }

    fn shutdown(self) -> Shutdown {
        let shutdown = Shutdown {
            state: Arc::clone(&self.shutdown),
        };
        drop(self);
        shutdown
    }
}

pub(super) struct Shutdown {
    state: Arc<Mutex<ShutdownState>>,
}

impl Future for Shutdown {
    type Output = ();

    fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<()> {
        let mut state = self
            .state
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        match &mut *state {
            ShutdownState::Complete => Poll::Ready(()),
            ShutdownState::Waiting(waker) => {
                *waker = Some(cx.waker().clone());
                Poll::Pending
            }
        }
    }
}
use std::convert::Infallible;
