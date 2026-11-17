use action::{Action, ActionContext, ActionError, ActionHandle};
use execution::{CancellationSource, Execution, ExecutionOptions, ExecutionStatus, NodeOutcome};
use graph::{Condition, Edge, Graph, Step};
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering},
};

struct Count(Arc<AtomicUsize>);
impl Action for Count {
    type Inputs = ();
    type Output = usize;
    fn execute(&self, _: &ActionContext, (): ()) -> Result<usize, ActionError> {
        Ok(self.0.fetch_add(1, Ordering::SeqCst))
    }
}

enum Finish {
    Success,
    Cancel,
    Fail,
}
struct CancelInside {
    source: CancellationSource,
    finish: Finish,
}
impl Action for CancelInside {
    type Inputs = ();
    type Output = usize;
    fn execute(&self, ctx: &ActionContext, (): ()) -> Result<usize, ActionError> {
        self.source.cancel();
        match self.finish {
            Finish::Success => Ok(42),
            Finish::Cancel => {
                ctx.control().require_running()?;
                Ok(0)
            }
            Finish::Fail => Err(ActionError::new("真实失败")),
        }
    }
}

#[test]
fn pre_cancel_never_runs_actions_and_does_not_leak_between_executions() {
    let count = Arc::new(AtomicUsize::new(0));
    let step = Step::new("work", ActionHandle::new(Count(Arc::clone(&count)))).unwrap();
    let graph = Graph::new("root", [step.into()], []).unwrap();
    let compilation = Arc::new(graph.compile().unwrap());
    let source = CancellationSource::new();
    source.cancel();
    let result = Execution::new(
        Arc::clone(&compilation),
        ExecutionOptions::new(1)
            .unwrap()
            .cancellation(source.token()),
    )
    .run()
    .unwrap();
    assert_eq!(result.status(), ExecutionStatus::Cancelled);
    assert_eq!(count.load(Ordering::SeqCst), 0);
    let second = Execution::new(compilation, ExecutionOptions::new(1).unwrap())
        .run()
        .unwrap();
    assert_eq!(second.status(), ExecutionStatus::Succeeded);
    assert_eq!(count.load(Ordering::SeqCst), 1);
}

#[test]
fn cancellation_retains_success_or_real_failure_and_stops_dependents() {
    for finish in [Finish::Success, Finish::Cancel, Finish::Fail] {
        let expected = match finish {
            Finish::Fail => ExecutionStatus::Failed,
            _ => ExecutionStatus::Cancelled,
        };
        let source = CancellationSource::new();
        let count = Arc::new(AtomicUsize::new(0));
        let work = Step::new(
            "work",
            ActionHandle::new(CancelInside {
                source: source.clone(),
                finish,
            }),
        )
        .unwrap();
        let tail = Step::new("tail", ActionHandle::new(Count(Arc::clone(&count)))).unwrap();
        let edge = Edge::new(&work, &tail);
        let graph = Graph::new("root", [work.into(), tail.into()], [edge]).unwrap();
        let result = Execution::new(
            graph.compile().unwrap(),
            ExecutionOptions::new(2)
                .unwrap()
                .cancellation(source.token()),
        )
        .run()
        .unwrap();
        assert_eq!(result.status(), expected);
        assert_eq!(count.load(Ordering::SeqCst), 0);
        assert!(
            result
                .nodes()
                .any(|(_, value)| matches!(value, NodeOutcome::Cancelled))
        );
    }
}

#[tokio::test(flavor = "current_thread")]
async fn async_cancellation_wakes_a_driver_with_only_a_pending_predicate() {
    let source = CancellationSource::new();
    let started = Arc::new(tokio::sync::Notify::new());
    let seen = Arc::clone(&started);
    let predicate = Condition::new_async(
        move |(): ()| {
            let seen = Arc::clone(&seen);
            async move {
                seen.notify_one();
                std::future::pending::<Result<bool, ActionError>>().await
            }
        },
        [],
    )
    .unwrap();
    let count = Arc::new(AtomicUsize::new(0));
    let step = Step::with_condition("guarded", ActionHandle::new(Count(count)), predicate).unwrap();
    let graph = Graph::new("root", [step.into()], []).unwrap();
    let execution = Execution::new(
        graph.compile().unwrap(),
        ExecutionOptions::new(1)
            .unwrap()
            .cancellation(source.token()),
    );
    let cancel = async {
        started.notified().await;
        source.cancel();
    };
    let (result, ()) = tokio::time::timeout(std::time::Duration::from_secs(3), async {
        tokio::join!(execution.run_async(), cancel)
    })
    .await
    .unwrap();
    assert_eq!(result.unwrap().status(), ExecutionStatus::Cancelled);
}

#[test]
fn a_successful_running_root_does_not_erase_execution_cancellation() {
    let source = CancellationSource::new();
    let work = Step::new(
        "work",
        ActionHandle::new(CancelInside {
            source: source.clone(),
            finish: Finish::Success,
        }),
    )
    .unwrap();
    let compilation = graph::Projection::from_node(work.into())
        .unwrap()
        .compile()
        .unwrap();
    let outcome = Execution::new(
        compilation,
        ExecutionOptions::new(1)
            .unwrap()
            .cancellation(source.token()),
    )
    .run()
    .unwrap();
    assert_eq!(outcome.status(), ExecutionStatus::Cancelled);
    assert!(
        outcome
            .nodes()
            .all(|(_, value)| matches!(value, NodeOutcome::Succeeded(_)))
    );
}
