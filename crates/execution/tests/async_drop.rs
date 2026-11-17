use std::future::{Future, pending};
use std::pin::pin;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Condvar, Mutex, mpsc};
use std::task::{Context, Poll, Wake, Waker};
use std::thread;
use std::time::Duration;

use action::{Action, ActionContext, ActionError, ActionHandle};
use execution::{Execution, ExecutionOptions, ExecutionStatus};
use graph::{Condition, Graph, Step};

struct NoopWake;
impl Wake for NoopWake {
    fn wake(self: Arc<Self>) {}
}

struct Count(Arc<AtomicUsize>);
impl Action for Count {
    type Inputs = ();
    type Output = ();
    fn execute(&self, _: &ActionContext, (): ()) -> Result<(), ActionError> {
        self.0.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
}

#[test]
fn runtime_independent_ready_predicate_needs_no_tokio_or_workers() {
    let calls = Arc::new(AtomicUsize::new(0));
    let condition = Condition::new_async(|()| async { Ok(false) }, []).unwrap();
    let step = Step::with_condition(
        "skipped",
        ActionHandle::new(Count(Arc::clone(&calls))),
        condition,
    )
    .unwrap();
    let graph = Graph::new("no runtime", [step.into()], []).unwrap();
    let execution = Execution::new(graph.compile().unwrap(), ExecutionOptions::new(1).unwrap());
    let mut future = pin!(execution.run_async());
    let waker = Waker::from(Arc::new(NoopWake));
    let Poll::Ready(Ok(outcome)) = future.as_mut().poll(&mut Context::from_waker(&waker)) else {
        panic!("ready-only graph must finish without a runtime");
    };
    assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
    assert_eq!(calls.load(Ordering::SeqCst), 0);
}

struct DropProbe(Arc<AtomicBool>);
impl Drop for DropProbe {
    fn drop(&mut self) {
        self.0.store(true, Ordering::SeqCst);
    }
}

struct Blocked {
    entered: mpsc::Sender<()>,
    gate: Arc<(Mutex<bool>, Condvar)>,
    finished: mpsc::Sender<()>,
}
impl Action for Blocked {
    type Inputs = ();
    type Output = ();
    fn execute(&self, _: &ActionContext, (): ()) -> Result<(), ActionError> {
        self.entered.send(()).unwrap();
        let (lock, ready) = &*self.gate;
        let mut open = lock.lock().unwrap();
        while !*open {
            open = ready.wait(open).unwrap();
        }
        self.finished.send(()).unwrap();
        Ok(())
    }
}

#[test]
fn dropping_execution_drops_predicates_without_joining_a_blocked_action() {
    let dropped = Arc::new(AtomicBool::new(false));
    let observed = Arc::clone(&dropped);
    let condition = Condition::new_async(
        move |()| {
            let guard = DropProbe(Arc::clone(&observed));
            async move {
                let _guard = guard;
                pending::<Result<bool, ActionError>>().await
            }
        },
        [],
    )
    .unwrap();
    let calls = Arc::new(AtomicUsize::new(0));
    let gated = Step::with_condition("gated", ActionHandle::new(Count(calls)), condition).unwrap();
    let gate = Arc::new((Mutex::new(false), Condvar::new()));
    let (entered, started) = mpsc::channel();
    let (finished, completed) = mpsc::channel();
    let action = Step::new(
        "blocked",
        ActionHandle::new(Blocked {
            entered,
            gate: Arc::clone(&gate),
            finished,
        }),
    )
    .unwrap();
    let graph = Graph::new("cancel", [action.into(), gated.into()], []).unwrap();
    let execution = Execution::new(graph.compile().unwrap(), ExecutionOptions::new(1).unwrap());
    let (request, stop) = mpsc::channel();
    let (exited, returned) = mpsc::channel();
    let driver = thread::spawn(move || {
        let mut future = Box::pin(execution.run_async());
        let waker = Waker::from(Arc::new(NoopWake));
        assert!(
            future
                .as_mut()
                .poll(&mut Context::from_waker(&waker))
                .is_pending()
        );
        stop.recv().unwrap();
        drop(future);
        exited.send(()).unwrap();
    });
    started
        .recv_timeout(Duration::from_secs(3))
        .expect("action never started");
    request.send(()).unwrap();
    // 超时也先放行动作，保证回归时不会把整套测试永久挂住。
    let nonblocking = returned.recv_timeout(Duration::from_secs(2));
    let predicate_dropped = dropped.load(Ordering::SeqCst);
    *gate.0.lock().unwrap() = true;
    gate.1.notify_all();
    completed
        .recv_timeout(Duration::from_secs(3))
        .expect("submitted action did not finish");
    driver.join().unwrap();
    assert!(
        nonblocking.is_ok(),
        "execution drop blocked on the action worker"
    );
    assert!(
        predicate_dropped,
        "pending predicate was retained after execution drop"
    );
}
