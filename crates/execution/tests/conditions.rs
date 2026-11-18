use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::Duration;

use rivlet_action::{Action, ActionContext, ActionError, ActionHandle};
use rivlet_core::{BlockCause, NodeId};
use rivlet_execution::{
    ConditionOutcome, Execution, ExecutionOptions, ExecutionOutcome, ExecutionStatus, NodeOutcome,
};
use rivlet_graph::{Compilation, Condition, Edge, Graph, GraphInterface, Projection, Step};

fn id(compilation: &Compilation, path: &[usize]) -> NodeId {
    // 原先只编译内部结构；现在完整 Graph 根也具有自己的挂载路径。
    let path = [&[0], path].concat();
    (0..compilation.graph().nodes().len())
        .map(NodeId::new)
        .find(|id| compilation.node(*id).path() == path.as_slice())
        .unwrap()
}
fn execute(body: Graph, workers: usize) -> ExecutionOutcome {
    Execution::new(
        Projection::from_node(body.into())
            .unwrap()
            .compile()
            .unwrap(),
        ExecutionOptions::new(workers).unwrap(),
    )
    .run()
    .unwrap()
}
fn value(outcome: &ExecutionOutcome, node: NodeId) -> Arc<usize> {
    let Some(NodeOutcome::Succeeded(value)) = outcome.node(node) else {
        panic!("expected success")
    };
    value.downcast::<usize>().unwrap()
}
struct Number(usize);
impl Action for Number {
    type Inputs = ();
    type Output = usize;
    fn execute(&self, _: &ActionContext, (): ()) -> Result<usize, ActionError> {
        Ok(self.0)
    }
}
struct Double;
impl Action for Double {
    type Inputs = (Option<Arc<usize>>,);
    type Output = usize;
    fn execute(&self, _: &ActionContext, (value,): Self::Inputs) -> Result<usize, ActionError> {
        Ok(value.map_or(0, |value| *value * 2))
    }
}
struct Never;
impl Action for Never {
    type Inputs = ();
    type Output = usize;
    fn execute(&self, _: &ActionContext, (): ()) -> Result<usize, ActionError> {
        panic!("action must not run")
    }
}
struct Fail;
impl Action for Fail {
    type Inputs = ();
    type Output = usize;
    fn execute(&self, _: &ActionContext, (): ()) -> Result<usize, ActionError> {
        Err(ActionError::new("source failed"))
    }
}
fn number(label: &str, value: usize) -> Step {
    Step::new(label, ActionHandle::new(Number(value))).unwrap()
}

#[test]
fn node_predicate_and_action_receive_separate_typed_inputs() {
    let decision = number("decision", 1);
    let source = number("source", 21);
    let calls = Arc::new(AtomicUsize::new(0));
    let captured = Arc::clone(&calls);
    let condition = Condition::new(
        move |(decision,): (Option<Arc<usize>>,)| {
            captured.fetch_add(1, Ordering::SeqCst);
            Ok(decision.is_some_and(|value| *value == 1))
        },
        [(&decision).into()],
    )
    .unwrap();
    let apply = Step::with_condition("apply", ActionHandle::new(Double), condition).unwrap();
    let body = Graph::new(
        "scope",
        [decision.into(), source.clone().into(), apply.clone().into()],
        [Edge::results([&source], &apply).unwrap()],
    )
    .unwrap();
    let outcome = execute(body, 2);
    let applied = id(outcome.compilation(), &[2]);
    assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
    assert_eq!(*value(&outcome, applied), 42);
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    assert!(matches!(
        outcome.condition(outcome.compilation().node(applied).condition().unwrap()),
        Some(ConditionOutcome::Resolved(true))
    ));
}

#[test]
fn false_node_condition_produces_missing_inputs_not_failed_outputs() {
    let condition = Condition::new(|()| Ok(false), []).unwrap();
    let source = Step::with_condition("skipped", ActionHandle::new(Never), condition).unwrap();
    let target = Step::new("target", ActionHandle::new(Double)).unwrap();
    let outcome = execute(
        Graph::new(
            "scope",
            [source.clone().into(), target.clone().into()],
            [Edge::results([&source], &target).unwrap()],
        )
        .unwrap(),
        1,
    );
    assert!(matches!(
        outcome.node(id(outcome.compilation(), &[0])),
        Some(NodeOutcome::Skipped)
    ));
    assert_eq!(*value(&outcome, id(outcome.compilation(), &[1])), 0);
    assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
}

#[test]
fn graph_condition_skips_its_whole_body_but_not_independent_work() {
    let calls = Arc::new(AtomicUsize::new(0));
    let captured = Arc::clone(&calls);
    let child_condition = Condition::new(
        move |()| {
            captured.fetch_add(1, Ordering::SeqCst);
            Ok(true)
        },
        [],
    )
    .unwrap();
    let child = Step::with_condition("never", ActionHandle::new(Never), child_condition).unwrap();
    let graph = Graph::with_condition(
        "skipped",
        [child.into()],
        [],
        GraphInterface::default(),
        Condition::new(|()| Ok(false), []).unwrap(),
    )
    .unwrap();
    let outcome = execute(
        Graph::new(
            "scope",
            [graph.into(), number("independent", 42).into()],
            [],
        )
        .unwrap(),
        2,
    );
    assert!(matches!(
        outcome.node(id(outcome.compilation(), &[0])),
        Some(NodeOutcome::Skipped)
    ));
    assert!(matches!(
        outcome.node(id(outcome.compilation(), &[0, 0])),
        Some(NodeOutcome::Skipped)
    ));
    assert_eq!(*value(&outcome, id(outcome.compilation(), &[1])), 42);
    assert_eq!(calls.load(Ordering::SeqCst), 0);
    let child_condition = outcome
        .compilation()
        .node(id(outcome.compilation(), &[0, 0]))
        .condition()
        .unwrap();
    assert!(matches!(
        outcome.condition(child_condition),
        Some(ConditionOutcome::NotEvaluated)
    ));
}

#[test]
fn predicate_failure_keeps_its_error_and_only_blocks_dependents() {
    let original = ActionError::new("policy failed");
    let captured = original.clone();
    let guarded = Step::with_condition(
        "guarded",
        ActionHandle::new(Never),
        Condition::new(move |()| Err(captured.clone()), []).unwrap(),
    )
    .unwrap();
    let downstream = Step::new("downstream", ActionHandle::new(Never)).unwrap();
    let outcome = execute(
        Graph::new(
            "scope",
            [
                guarded.clone().into(),
                downstream.clone().into(),
                number("independent", 42).into(),
            ],
            [Edge::new(&guarded, &downstream)],
        )
        .unwrap(),
        2,
    );
    let guarded = id(outcome.compilation(), &[0]);
    assert_eq!(outcome.status(), ExecutionStatus::Failed);
    assert!(matches!(
        outcome.node(guarded),
        Some(NodeOutcome::Blocked(BlockCause::Condition(_)))
    ));
    assert!(matches!(
        outcome.node(id(outcome.compilation(), &[1])),
        Some(NodeOutcome::Blocked(BlockCause::Prerequisite(_)))
    ));
    assert!(
        matches!(outcome.condition(outcome.compilation().node(guarded).condition().unwrap()), Some(ConditionOutcome::Failed(error)) if error == &original)
    );
    assert_eq!(*value(&outcome, id(outcome.compilation(), &[2])), 42);
}

#[test]
fn failed_condition_input_never_invokes_its_predicate() {
    let source = Step::new("source", ActionHandle::new(Fail)).unwrap();
    let calls = Arc::new(AtomicUsize::new(0));
    let captured = Arc::clone(&calls);
    let condition = Condition::new(
        move |(_value,): (Option<Arc<usize>>,)| {
            captured.fetch_add(1, Ordering::SeqCst);
            Ok(true)
        },
        [(&source).into()],
    )
    .unwrap();
    let guarded = Step::with_condition("guarded", ActionHandle::new(Never), condition).unwrap();
    let outcome = execute(
        Graph::new("scope", [source.into(), guarded.into()], []).unwrap(),
        2,
    );
    let guarded = id(outcome.compilation(), &[1]);
    assert_eq!(calls.load(Ordering::SeqCst), 0);
    assert!(matches!(
        outcome.node(guarded),
        Some(NodeOutcome::Blocked(BlockCause::ConditionInput { .. }))
    ));
    assert!(matches!(
        outcome.condition(outcome.compilation().node(guarded).condition().unwrap()),
        Some(ConditionOutcome::NotEvaluated)
    ));
}

#[test]
fn predicate_panic_is_isolated_like_an_action_failure() {
    let guarded = Step::with_condition(
        "guarded",
        ActionHandle::new(Never),
        Condition::new(|()| panic!("predicate panic"), []).unwrap(),
    )
    .unwrap();
    let outcome = execute(
        Graph::new("scope", [guarded.into(), number("good", 42).into()], []).unwrap(),
        2,
    );
    let guarded = id(outcome.compilation(), &[0]);
    assert!(
        matches!(outcome.condition(outcome.compilation().node(guarded).condition().unwrap()), Some(ConditionOutcome::Failed(error)) if error.to_string() == "native predicate panicked")
    );
    assert_eq!(*value(&outcome, id(outcome.compilation(), &[1])), 42);
}

struct Count(Arc<AtomicUsize>);
impl Action for Count {
    type Inputs = ();
    type Output = usize;
    fn execute(&self, _: &ActionContext, (): ()) -> Result<usize, ActionError> {
        Ok(self.0.fetch_add(1, Ordering::SeqCst) + 1)
    }
}

#[test]
fn shared_structure_predicates_bind_each_mounts_own_result() {
    let source = Step::new("source", ActionHandle::new(Count(Arc::default()))).unwrap();
    let condition = Condition::new(
        |(value,): (Option<Arc<usize>>,)| Ok(value.is_some_and(|value| *value == 1)),
        [(&source).into()],
    )
    .unwrap();
    let guarded =
        Step::with_condition("guarded", ActionHandle::new(Number(42)), condition).unwrap();
    let body = Arc::new(Graph::new("scope", [source.into(), guarded.into()], []).unwrap());
    let left = Graph::with_interface(
        "left",
        body.nodes().iter().cloned(),
        body.edges().iter().cloned(),
        body.interface().declaration().clone(),
    )
    .unwrap();
    let right = Graph::with_interface(
        "right",
        body.nodes().iter().cloned(),
        body.edges().iter().cloned(),
        body.interface().declaration().clone(),
    )
    .unwrap();
    let outcome = execute(
        Graph::new("scope", [left.into(), right.into()], []).unwrap(),
        2,
    );
    for prefix in [0, 1] {
        let source = *value(&outcome, id(outcome.compilation(), &[prefix, 0]));
        let guarded = outcome.node(id(outcome.compilation(), &[prefix, 1]));
        assert_eq!(
            matches!(guarded, Some(NodeOutcome::Succeeded(_))),
            source == 1
        );
        assert_eq!(matches!(guarded, Some(NodeOutcome::Skipped)), source == 2);
    }
}

#[test]
fn shared_compilation_rechecks_graph_conditions_in_each_execution() {
    let source = Step::new("source", ActionHandle::new(Count(Arc::default()))).unwrap();
    let calls = Arc::new(AtomicUsize::new(0));
    let captured = Arc::clone(&calls);
    let condition = Condition::new(
        move |(value,): (Option<Arc<usize>>,)| {
            captured.fetch_add(1, Ordering::SeqCst);
            Ok(value.is_some_and(|value| *value == 1))
        },
        [(&source).into()],
    )
    .unwrap();
    let child = Graph::with_condition(
        "child",
        [number("result", 42).into()],
        [],
        GraphInterface::default(),
        condition,
    )
    .unwrap();
    let compilation = Arc::new(
        Graph::new("scope", [source.into(), child.into()], [])
            .unwrap()
            .compile()
            .unwrap(),
    );
    let child = id(&compilation, &[1]);
    let condition = compilation.graph().nodes()[child.index()]
        .condition()
        .unwrap();
    for expected in [true, false] {
        let outcome = Execution::new(Arc::clone(&compilation), ExecutionOptions::new(1).unwrap())
            .run()
            .unwrap();
        assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
        assert!(
            matches!(outcome.condition(condition), Some(ConditionOutcome::Resolved(value)) if *value == expected)
        );
        assert_eq!(
            matches!(outcome.node(child), Some(NodeOutcome::Skipped)),
            !expected
        );
        let descendant = outcome.node(id(outcome.compilation(), &[1, 0]));
        assert_eq!(
            matches!(descendant, Some(NodeOutcome::Succeeded(_))),
            expected
        );
        assert_eq!(matches!(descendant, Some(NodeOutcome::Skipped)), !expected);
    }
    assert_eq!(calls.load(Ordering::SeqCst), 2);
}

struct Wait(Arc<(Mutex<bool>, Condvar)>);
impl Action for Wait {
    type Inputs = ();
    type Output = ();
    fn execute(&self, _: &ActionContext, (): ()) -> Result<(), ActionError> {
        let (lock, ready) = &*self.0;
        let (released, _) = ready
            .wait_timeout_while(lock.lock().unwrap(), Duration::from_secs(3), |released| {
                !*released
            })
            .unwrap();
        if *released {
            Ok(())
        } else {
            Err(ActionError::new("consumer did not release producer"))
        }
    }
}
struct Release(Arc<(Mutex<bool>, Condvar)>);
impl Action for Release {
    type Inputs = ();
    type Output = ();
    fn execute(&self, _: &ActionContext, (): ()) -> Result<(), ActionError> {
        let (lock, ready) = &*self.0;
        *lock.lock().unwrap() = true;
        ready.notify_all();
        Ok(())
    }
}

#[test]
fn false_edge_condition_releases_consumer_without_cancelling_source() {
    let release = Arc::new((Mutex::new(false), Condvar::new()));
    let source = Step::new("wait", ActionHandle::new(Wait(Arc::clone(&release)))).unwrap();
    let target = Step::new("release", ActionHandle::new(Release(release))).unwrap();
    let edge = Edge::with_condition(
        &source,
        &target,
        Condition::new(|()| Ok(false), []).unwrap(),
    );
    let outcome = execute(
        Graph::new("scope", [source.into(), target.into()], [edge]).unwrap(),
        2,
    );
    assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
    assert!(
        outcome
            .nodes()
            .all(|(_, result)| matches!(result, NodeOutcome::Succeeded(_)))
    );
}

#[test]
fn conditional_ports_and_forwarded_predicate_inputs_preserve_interleaving() {
    let source = number("source", 21);
    let finish = number("finish", 1);
    let inner = Graph::with_interface(
        "inner",
        [source.clone().into(), finish.clone().into()],
        [Edge::new(&source, &finish)],
        GraphInterface::default()
            .output("plan", &source)
            .unwrap()
            .input("finish", [&finish])
            .unwrap(),
    )
    .unwrap();
    let provider = Graph::with_interface(
        "provider",
        [inner.clone().into()],
        [],
        GraphInterface::default()
            .output("plan", inner.interface().output("plan").unwrap())
            .unwrap()
            .input("finish", [inner.interface().input("finish").unwrap()])
            .unwrap(),
    )
    .unwrap();
    let apply = Step::new("apply", ActionHandle::new(Double)).unwrap();
    let consumer = Graph::with_interface(
        "consumer",
        [apply.clone().into()],
        [],
        GraphInterface::default()
            .input("apply", [&apply])
            .unwrap()
            .output("done", &apply)
            .unwrap(),
    )
    .unwrap();
    let condition = Condition::new(
        |(value,): (Option<Arc<usize>>,)| Ok(value.is_some_and(|value| *value == 21)),
        [provider.interface().output("plan").unwrap().into()],
    )
    .unwrap();
    let outcome = execute(
        Graph::new(
            "scope",
            [provider.clone().into(), consumer.clone().into()],
            [
                Edge::results(
                    [provider.interface().output("plan").unwrap()],
                    consumer.interface().input("apply").unwrap(),
                )
                .unwrap(),
                Edge::ports_with_condition(
                    consumer.interface().output("done").unwrap(),
                    provider.interface().input("finish").unwrap(),
                    condition,
                ),
            ],
        )
        .unwrap(),
        2,
    );
    assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
    assert_eq!(*value(&outcome, id(outcome.compilation(), &[1, 0])), 42);
    let finish = id(outcome.compilation(), &[0, 0, 1]);
    let condition = outcome.compilation().graph().nodes()[finish.index()]
        .prerequisites()
        .iter()
        .find_map(|prerequisite| prerequisite.condition())
        .unwrap();
    assert!(matches!(
        outcome.condition(condition),
        Some(ConditionOutcome::Resolved(true))
    ));
}

#[test]
fn compiled_predicates_accept_skipped_inputs() {
    let source = number("source", 21);
    let condition = Condition::new(
        |(value,): (Option<Arc<usize>>,)| Ok(value.is_none()),
        [(&source).into()],
    )
    .unwrap();
    let guarded =
        Step::with_condition("guarded", ActionHandle::new(Number(42)), condition).unwrap();
    let compilation = Graph::new("scope", [source.into(), guarded.into()], [])
        .unwrap()
        .compile()
        .unwrap();
    let skipped = id(&compilation, &[0]);
    let outcome = Execution::new(
        compilation,
        ExecutionOptions::new(1).unwrap().skip([skipped]),
    )
    .run()
    .unwrap();
    assert_eq!(*value(&outcome, id(outcome.compilation(), &[1])), 42);
}

struct Payload(Vec<u8>);
struct Produce;
impl Action for Produce {
    type Inputs = ();
    type Output = Payload;
    fn execute(&self, _: &ActionContext, (): ()) -> Result<Payload, ActionError> {
        Ok(Payload(vec![42]))
    }
}

#[test]
fn predicates_share_the_actual_nonclone_payload_without_serialization() {
    let source = Step::new("produce", ActionHandle::new(Produce)).unwrap();
    let seen: Arc<Mutex<Vec<Arc<Payload>>>> = Arc::default();
    let captured = Arc::clone(&seen);
    let condition = Condition::new(
        move |(value,): (Option<Arc<Payload>>,)| {
            let value = value.ok_or_else(|| ActionError::new("payload missing"))?;
            assert_eq!(value.0, [42]);
            captured.lock().unwrap().push(value);
            Ok(true)
        },
        [(&source).into()],
    )
    .unwrap();
    let a = Step::with_condition("a", ActionHandle::new(Number(1)), condition.clone()).unwrap();
    let b = Step::with_condition("b", ActionHandle::new(Number(2)), condition).unwrap();
    let outcome = execute(
        Graph::new("scope", [source.into(), a.into(), b.into()], []).unwrap(),
        2,
    );
    assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
    let seen = seen.lock().unwrap();
    assert_eq!(seen.len(), 2);
    assert!(Arc::ptr_eq(&seen[0], &seen[1]));
}
