use std::future::Future;
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Duration;

use action::{Action, ActionContext, ActionError, ActionHandle};
use execution::{
    ConditionOutcome, Execution, ExecutionError, ExecutionOptions, ExecutionOutcome,
    ExecutionStatus, NodeOutcome,
};
use graph::{Condition, Edge, Graph, GraphInterface, PortName, Step};
use rivlet::{ConditionId, NodeId};
use tokio::sync::Semaphore;

fn block_on<F: Future>(future: F) -> F::Output {
    tokio::runtime::Builder::new_current_thread()
        .enable_time()
        .build()
        .unwrap()
        .block_on(async {
            tokio::time::timeout(Duration::from_secs(3), future)
                .await
                .expect("execution stalled")
        })
}

fn execute(body: &Graph) -> ExecutionOutcome {
    fn require_send<F: Future + Send>(future: F) -> F {
        future
    }
    let future =
        Execution::new(body.compile().unwrap(), ExecutionOptions::new(1).unwrap()).run_async();
    block_on(require_send(future)).unwrap()
}

fn mounted(outcome: &ExecutionOutcome, path: &[usize]) -> NodeId {
    let path = [&[0], path].concat();
    (0..outcome.compilation().graph().nodes().len())
        .map(NodeId::new)
        .find(|node| outcome.compilation().node(*node).path() == path)
        .unwrap()
}

struct Number(u32);
impl Action for Number {
    type Inputs = ();
    type Output = u32;
    fn execute(&self, _: &ActionContext, (): ()) -> Result<u32, ActionError> {
        Ok(self.0)
    }
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

struct Double;
impl Action for Double {
    type Inputs = (Option<Arc<u32>>,);
    type Output = u32;
    fn execute(&self, _: &ActionContext, (value,): Self::Inputs) -> Result<u32, ActionError> {
        Ok(*value.ok_or_else(|| ActionError::new("missing number"))? * 2)
    }
}

fn counter(label: &str, calls: &Arc<AtomicUsize>) -> Step {
    Step::new(label, ActionHandle::new(Count(Arc::clone(calls)))).unwrap()
}

#[test]
fn async_predicate_and_action_receive_separate_inputs() {
    let decision = Step::new("decision", ActionHandle::new(Number(22))).unwrap();
    let data = Step::new("data", ActionHandle::new(Number(7))).unwrap();
    let condition = Condition::new_async(
        |(value,): (Option<Arc<u32>>,)| async move {
            tokio::time::sleep(Duration::from_millis(1)).await;
            Ok(value.is_some_and(|value| *value == 22))
        },
        [(&decision).into()],
    )
    .unwrap();
    let work = Step::with_condition("work", ActionHandle::new(Double), condition).unwrap();
    let graph = Graph::new(
        "inputs",
        [decision.into(), data.clone().into(), work.clone().into()],
        [Edge::results([&data], &work).unwrap()],
    )
    .unwrap();
    let outcome = execute(&graph);
    assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
    let Some(NodeOutcome::Succeeded(value)) = outcome.node(mounted(&outcome, &[2])) else {
        panic!("missing result");
    };
    assert_eq!(*value.downcast::<u32>().unwrap(), 14);
}

#[test]
fn sync_run_rejects_async_before_actions_or_factories_start() {
    let calls = Arc::new(AtomicUsize::new(0));
    let factories = Arc::new(AtomicUsize::new(0));
    let observed = Arc::clone(&factories);
    let condition = Condition::new_async(
        move |()| {
            observed.fetch_add(1, Ordering::SeqCst);
            async { Ok(true) }
        },
        [],
    )
    .unwrap();
    let gated = Step::with_condition("gated", ActionHandle::new(Number(1)), condition).unwrap();
    let graph = Graph::new(
        "reject",
        [counter("independent", &calls).into(), gated.into()],
        [],
    )
    .unwrap();
    let result = Execution::new(graph.compile().unwrap(), ExecutionOptions::new(1).unwrap()).run();
    assert!(matches!(result, Err(ExecutionError::AsyncPredicate(_))));
    assert_eq!(calls.load(Ordering::SeqCst), 0);
    assert_eq!(factories.load(Ordering::SeqCst), 0);
}

fn rendezvous(signal: Arc<Semaphore>, wait: Arc<Semaphore>) -> Condition {
    Condition::new_async(
        move |()| {
            let (signal, wait) = (Arc::clone(&signal), Arc::clone(&wait));
            async move {
                signal.add_permits(1);
                wait.acquire()
                    .await
                    .map_err(|error| ActionError::new(error.to_string()))?
                    .forget();
                Ok(true)
            }
        },
        [],
    )
    .unwrap()
}

#[test]
fn independent_predicates_are_polled_concurrently_without_using_action_slots() {
    let left = Arc::new(Semaphore::new(0));
    let right = Arc::new(Semaphore::new(0));
    let calls = Arc::new(AtomicUsize::new(0));
    let a = Step::with_condition(
        "a",
        ActionHandle::new(Count(Arc::clone(&calls))),
        rendezvous(Arc::clone(&left), Arc::clone(&right)),
    )
    .unwrap();
    let b = Step::with_condition(
        "b",
        ActionHandle::new(Count(Arc::clone(&calls))),
        rendezvous(right, left),
    )
    .unwrap();
    let graph = Graph::new("rendezvous", [a.into(), b.into()], []).unwrap();
    assert_eq!(execute(&graph).status(), ExecutionStatus::Succeeded);
    assert_eq!(calls.load(Ordering::SeqCst), 2);
}

struct Signal(Arc<Semaphore>);
impl Action for Signal {
    type Inputs = ();
    type Output = ();
    fn execute(&self, _: &ActionContext, (): ()) -> Result<(), ActionError> {
        self.0.add_permits(1);
        Ok(())
    }
}

#[test]
fn outstanding_predicate_survives_last_unrelated_action_completion() {
    let signal = Arc::new(Semaphore::new(0));
    let captured = Arc::clone(&signal);
    let condition = Condition::new_async(
        move |()| {
            let captured = Arc::clone(&captured);
            async move {
                captured
                    .acquire()
                    .await
                    .map_err(|error| ActionError::new(error.to_string()))?
                    .forget();
                tokio::time::sleep(Duration::from_millis(10)).await;
                Ok(true)
            }
        },
        [],
    )
    .unwrap();
    let gated = Step::with_condition("gated", ActionHandle::new(Number(42)), condition).unwrap();
    let independent = Step::new("signal", ActionHandle::new(Signal(signal))).unwrap();
    let graph = Graph::new("pending", [gated.into(), independent.into()], []).unwrap();
    assert_eq!(execute(&graph).status(), ExecutionStatus::Succeeded);
}

#[test]
fn every_execution_creates_a_fresh_future_without_running_it_during_compile() {
    let factories = Arc::new(AtomicUsize::new(0));
    let calls = Arc::new(AtomicUsize::new(0));
    let observed = Arc::clone(&factories);
    let condition = Condition::new_async(
        move |()| {
            let enabled = observed.fetch_add(1, Ordering::SeqCst) == 0;
            async move { Ok(enabled) }
        },
        [],
    )
    .unwrap();
    let step = Step::with_condition(
        "toggle",
        ActionHandle::new(Count(Arc::clone(&calls))),
        condition,
    )
    .unwrap();
    let graph = Graph::new("fresh", [step.into()], []).unwrap();
    let compilation = Arc::new(graph.compile().unwrap());
    assert_eq!(factories.load(Ordering::SeqCst), 0);
    for expected in [true, false] {
        let outcome = block_on(
            Execution::new(Arc::clone(&compilation), ExecutionOptions::new(1).unwrap()).run_async(),
        )
        .unwrap();
        assert!(
            matches!(outcome.condition(ConditionId::new(0)), Some(ConditionOutcome::Resolved(value)) if *value == expected)
        );
    }
    assert_eq!(factories.load(Ordering::SeqCst), 2);
    assert_eq!(calls.load(Ordering::SeqCst), 1);
}

#[derive(Clone, Copy)]
enum PanicSite {
    Factory,
    FirstPoll,
    LaterPoll,
}

#[test]
fn factory_and_future_panics_fail_only_their_dependents() {
    for site in [
        PanicSite::Factory,
        PanicSite::FirstPoll,
        PanicSite::LaterPoll,
    ] {
        let calls = Arc::new(AtomicUsize::new(0));
        let condition = Condition::new_async(
            move |()| {
                if matches!(site, PanicSite::Factory) {
                    panic!("factory panic");
                }
                async move {
                    if matches!(site, PanicSite::FirstPoll) {
                        panic!("poll panic");
                    }
                    tokio::task::yield_now().await;
                    if matches!(site, PanicSite::LaterPoll) {
                        panic!("later poll panic");
                    }
                    Ok(true)
                }
            },
            [],
        )
        .unwrap();
        let gated = Step::with_condition("gated", ActionHandle::new(Number(1)), condition).unwrap();
        let graph = Graph::new(
            "panic",
            [gated.into(), counter("independent", &calls).into()],
            [],
        )
        .unwrap();
        let outcome = execute(&graph);
        assert_eq!(outcome.status(), ExecutionStatus::Failed);
        assert!(matches!(
            outcome.condition(ConditionId::new(0)),
            Some(ConditionOutcome::Failed(_))
        ));
        assert_eq!(calls.load(Ordering::SeqCst), 1);
    }
}

#[test]
fn predicate_error_is_not_a_false_decision() {
    let condition = Condition::new_async(
        |()| async { Err(ActionError::new("approval unavailable")) },
        [],
    )
    .unwrap();
    let step = Step::with_condition("gated", ActionHandle::new(Number(0)), condition).unwrap();
    let outcome = execute(&Graph::new("error", [step.into()], []).unwrap());
    assert!(
        matches!(outcome.condition(ConditionId::new(0)), Some(ConditionOutcome::Failed(error)) if error.to_string() == "approval unavailable")
    );
}

struct Fail;
impl Action for Fail {
    type Inputs = ();
    type Output = u32;
    fn execute(&self, _: &ActionContext, (): ()) -> Result<u32, ActionError> {
        Err(ActionError::new("input failed"))
    }
}

#[test]
fn failed_input_never_constructs_the_async_future() {
    let source = Step::new("source", ActionHandle::new(Fail)).unwrap();
    let factories = Arc::new(AtomicUsize::new(0));
    let captured = Arc::clone(&factories);
    let condition = Condition::new_async(
        move |(_value,): (Option<Arc<u32>>,)| {
            captured.fetch_add(1, Ordering::SeqCst);
            async { Ok(true) }
        },
        [(&source).into()],
    )
    .unwrap();
    let target = Step::with_condition("target", ActionHandle::new(Number(0)), condition).unwrap();
    let outcome =
        execute(&Graph::new("input failure", [source.into(), target.into()], []).unwrap());
    assert_eq!(outcome.status(), ExecutionStatus::Failed);
    assert_eq!(factories.load(Ordering::SeqCst), 0);
    assert!(matches!(
        outcome.condition(ConditionId::new(0)),
        Some(ConditionOutcome::NotEvaluated)
    ));
}

#[test]
fn skipped_input_and_successful_unit_remain_distinct() {
    let calls = Arc::new(AtomicUsize::new(0));
    let unit = counter("unit", &calls);
    let skipped = Step::with_condition(
        "skipped",
        ActionHandle::new(Count(Arc::clone(&calls))),
        Condition::new(|()| Ok(false), []).unwrap(),
    )
    .unwrap();
    let condition = Condition::new_async(
        |(present, missing): (Option<Arc<()>>, Option<Arc<()>>)| async move {
            Ok(present.is_some() && missing.is_none())
        },
        [(&unit).into(), (&skipped).into()],
    )
    .unwrap();
    let target = Step::with_condition(
        "target",
        ActionHandle::new(Count(Arc::clone(&calls))),
        condition,
    )
    .unwrap();
    let outcome =
        execute(&Graph::new("unit", [unit.into(), skipped.into(), target.into()], []).unwrap());
    assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
    assert_eq!(calls.load(Ordering::SeqCst), 2);
}

#[test]
fn async_graph_and_port_conditions_resolve_forwarded_outputs_per_use() {
    for number in [0, 21] {
        let source = Step::new("source", ActionHandle::new(Number(number))).unwrap();
        let provider = Graph::with_interface(
            "provider",
            [source.clone().into()],
            [],
            GraphInterface::default()
                .output(PortName::Anonymous, &source)
                .unwrap(),
        )
        .unwrap();
        let forwarded = Graph::with_interface(
            "forwarded",
            [provider.clone().into()],
            [],
            GraphInterface::default()
                .output(
                    PortName::Anonymous,
                    provider.interface().output(PortName::Anonymous).unwrap(),
                )
                .unwrap(),
        )
        .unwrap();
        let factories = Arc::new(AtomicUsize::new(0));
        let captured = Arc::clone(&factories);
        let condition = Condition::new_async(
            move |(value,): (Option<Arc<u32>>,)| {
                captured.fetch_add(1, Ordering::SeqCst);
                async move { Ok(value.is_some_and(|value| *value == 21)) }
            },
            [forwarded
                .interface()
                .output(PortName::Anonymous)
                .unwrap()
                .into()],
        )
        .unwrap();
        let calls = Arc::new(AtomicUsize::new(0));
        let task = counter("task", &calls);
        let consumer = Graph::with_condition(
            "consumer",
            [task.clone().into()],
            [],
            GraphInterface::default()
                .input(PortName::Anonymous, [&task])
                .unwrap(),
            condition.clone(),
        )
        .unwrap();
        let edge = Edge::ports_with_condition(
            forwarded.interface().output(PortName::Anonymous).unwrap(),
            consumer.interface().input(PortName::Anonymous).unwrap(),
            condition,
        );
        let outcome =
            execute(&Graph::new("ports", [forwarded.into(), consumer.into()], [edge]).unwrap());
        assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
        assert_eq!(calls.load(Ordering::SeqCst), usize::from(number == 21));
        assert_eq!(
            factories.load(Ordering::SeqCst),
            if number == 21 { 2 } else { 1 }
        );
    }
}
