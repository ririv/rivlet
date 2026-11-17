//! Cooperative cancellation with scoped, removable wake registrations.

use std::collections::BTreeMap;
use std::fmt::{self, Debug, Formatter};
use std::future::{Future, poll_fn};
use std::sync::{Arc, Mutex, Weak};
use std::task::Poll;

use crate::ActionError;

type Handler = Arc<dyn Fn() + Send + Sync>;

#[derive(Default)]
enum State {
    #[default]
    Open,
    Listening {
        next: usize,
        handlers: BTreeMap<usize, Handler>,
    },
    Cancelled,
}

/// The authority to cancel one explicitly shared execution scope.
#[derive(Clone, Default)]
pub struct CancellationSource(Arc<Mutex<State>>);

/// Read-only cancellation capability supplied to actions and execution drivers.
#[derive(Clone, Default)]
pub struct CancellationToken(Option<Arc<Mutex<State>>>);

impl PartialEq for CancellationToken {
    fn eq(&self, other: &Self) -> bool {
        match (&self.0, &other.0) {
            (None, None) => true,
            (Some(left), Some(right)) => Arc::ptr_eq(left, right),
            _ => false,
        }
    }
}
impl Eq for CancellationToken {}

impl Debug for CancellationToken {
    fn fmt(&self, f: &mut Formatter<'_>) -> fmt::Result {
        f.debug_struct("CancellationToken")
            .field("cancelled", &self.is_cancelled())
            .finish()
    }
}

impl CancellationSource {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    #[must_use]
    pub fn token(&self) -> CancellationToken {
        CancellationToken(Some(Arc::clone(&self.0)))
    }

    /// Publishes cancellation once and notifies registered waiters.
    ///
    /// # Panics
    /// Panics if a notification handler panics or the internal lock is poisoned.
    pub fn cancel(&self) {
        let previous = {
            let mut state = self.0.lock().expect("cancellation lock poisoned");
            std::mem::replace(&mut *state, State::Cancelled)
        };
        if let State::Listening { handlers, .. } = previous {
            let mut first_panic = None;
            for handler in handlers.into_values() {
                if let Err(error) =
                    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| handler()))
                {
                    if first_panic.is_none() {
                        first_panic = Some(error);
                    }
                }
            }
            if let Some(error) = first_panic {
                std::panic::resume_unwind(error);
            }
        }
    }
}

impl CancellationToken {
    #[must_use]
    /// # Panics
    /// Panics if the internal lock is poisoned.
    pub fn is_cancelled(&self) -> bool {
        self.0.as_ref().is_some_and(|state| {
            matches!(
                *state.lock().expect("cancellation lock poisoned"),
                State::Cancelled
            )
        })
    }

    /// Returns a typed cancellation acknowledgement, not an action failure.
    ///
    /// # Errors
    /// Returns cancellation when this token has been cancelled.
    pub fn require_running(&self) -> Result<(), ActionError> {
        if self.is_cancelled() {
            Err(ActionError::Cancelled)
        } else {
            Ok(())
        }
    }

    /// Registers one wakeup; dropping the registration unregisters it.
    ///
    /// # Panics
    /// Panics if the internal lock is poisoned, IDs overflow, or an immediate handler panics.
    #[must_use]
    pub fn subscribe(
        &self,
        handler: impl Fn() + Send + Sync + 'static,
    ) -> CancellationRegistration {
        let Some(shared) = &self.0 else {
            return CancellationRegistration {
                state: Weak::new(),
                id: None,
            };
        };
        let handler: Handler = Arc::new(handler);
        let mut state = shared.lock().expect("cancellation lock poisoned");
        if matches!(*state, State::Open) {
            *state = State::Listening {
                next: 0,
                handlers: BTreeMap::new(),
            };
        }
        let id = match &mut *state {
            State::Listening { next, handlers } => {
                let id = *next;
                *next = next
                    .checked_add(1)
                    .expect("cancellation registration overflow");
                handlers.insert(id, Arc::clone(&handler));
                Some(id)
            }
            State::Cancelled => None,
            State::Open => unreachable!(),
        };
        drop(state);
        if id.is_none() {
            handler();
        }
        CancellationRegistration {
            state: Arc::downgrade(shared),
            id,
        }
    }

    /// Waits without tying cancellation to a particular async runtime.
    pub fn cancelled(&self) -> impl Future<Output = ()> + '_ {
        let mut registration = None;
        poll_fn(move |cx| {
            if self.is_cancelled() {
                return Poll::Ready(());
            }
            let waker = cx.waker().clone();
            drop(registration.take());
            registration = Some(self.subscribe(move || waker.wake_by_ref()));
            if self.is_cancelled() {
                Poll::Ready(())
            } else {
                Poll::Pending
            }
        })
    }
}

/// Scoped subscription; unregisters callbacks not already selected for delivery.
pub struct CancellationRegistration {
    state: Weak<Mutex<State>>,
    id: Option<usize>,
}

impl Drop for CancellationRegistration {
    fn drop(&mut self) {
        if let (Some(state), Some(id)) = (self.state.upgrade(), self.id) {
            if let State::Listening { handlers, .. } =
                &mut *state.lock().expect("cancellation lock poisoned")
            {
                handlers.remove(&id);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::pin::pin;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::task::{Context, Wake, Waker};

    #[test]
    fn scoped_registration_unregisters_and_late_subscription_is_immediate() {
        let source = CancellationSource::new();
        let token = source.token();
        let count = Arc::new(AtomicUsize::new(0));
        let notified = Arc::clone(&count);
        let registration = token.subscribe(move || {
            notified.fetch_add(1, Ordering::SeqCst);
        });
        drop(registration);
        source.cancel();
        assert_eq!(count.load(Ordering::SeqCst), 0);
        let notified = Arc::clone(&count);
        let _late = token.subscribe(move || {
            notified.fetch_add(1, Ordering::SeqCst);
        });
        source.cancel();
        assert_eq!(count.load(Ordering::SeqCst), 1);
        assert_eq!(token.require_running(), Err(ActionError::Cancelled));
    }

    struct Counter(AtomicUsize);
    impl Wake for Counter {
        fn wake(self: Arc<Self>) {
            self.0.fetch_add(1, Ordering::SeqCst);
        }
    }

    #[test]
    fn cancellation_future_registers_one_wake_and_releases_it_on_drop() {
        let source = CancellationSource::new();
        let token = source.token();
        let counter = Arc::new(Counter(AtomicUsize::new(0)));
        let waker = Waker::from(Arc::clone(&counter));
        let mut cx = Context::from_waker(&waker);
        {
            let future = token.cancelled();
            let mut future = pin!(future);
            assert!(future.as_mut().poll(&mut cx).is_pending());
            assert!(future.as_mut().poll(&mut cx).is_pending());
        }
        source.cancel();
        assert_eq!(counter.0.load(Ordering::SeqCst), 0);
        assert!(pin!(token.cancelled()).poll(&mut cx).is_ready());
    }

    #[test]
    fn one_panicking_handler_does_not_hide_cancellation_from_other_waiters() {
        let source = CancellationSource::new();
        let token = source.token();
        let count = Arc::new(AtomicUsize::new(0));
        let _panic = token.subscribe(|| panic!("notification failed"));
        let notified = Arc::clone(&count);
        let _other = token.subscribe(move || {
            notified.fetch_add(1, Ordering::SeqCst);
        });
        assert!(
            std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| source.cancel())).is_err()
        );
        assert_eq!(count.load(Ordering::SeqCst), 1);
        assert!(token.is_cancelled());
    }
}
