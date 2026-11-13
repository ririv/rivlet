use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::Duration;

use action::{Action, ActionContext, ActionError, ActionHandle};
use execution::{
    Execution, ExecutionError, ExecutionOptions, ExecutionOutcome, ExecutionStatus, NodeFailure,
    NodeOutcome,
};
use graph::{Compilation, Edge, Graph, GraphInterface, GraphStructure, Step};
use rivlet::{BlockCause, NodeId};

fn node_id(compilation: &Compilation, path: &[usize]) -> NodeId {
    (0..compilation.graph().nodes().len())
        .map(NodeId::new)
        .find(|node| compilation.node(*node).path() == path)
        .unwrap()
}

fn value<T: Send + Sync + 'static>(outcome: &ExecutionOutcome, node: NodeId) -> Arc<T> {
    let Some(NodeOutcome::Succeeded(value)) = outcome.node(node) else {
        panic!("expected a successful result");
    };
    value.downcast::<T>().unwrap()
}

struct Plan(usize);
struct Design(Arc<AtomicUsize>);
impl Action for Design {
    type Inputs = ();
    type Output = Plan;
    fn execute(&self, _: &ActionContext, (): ()) -> Result<Plan, ActionError> {
        Ok(Plan(self.0.fetch_add(1, Ordering::SeqCst) + 1))
    }
}

struct Apply;
impl Action for Apply {
    type Inputs = (Option<Arc<Plan>>,);
    type Output = usize;
    fn execute(&self, _: &ActionContext, (plan,): Self::Inputs) -> Result<usize, ActionError> {
        Ok(plan.map_or(0, |plan| plan.0 * 2))
    }
}

#[test]
fn compilation_is_shared_but_executions_and_results_are_independent() {
    let calls = Arc::new(AtomicUsize::new(0));
    let design = Step::new("design", ActionHandle::new(Design(Arc::clone(&calls)))).unwrap();
    let apply = Step::new("apply", ActionHandle::new(Apply)).unwrap();
    let compilation = Arc::new(
        GraphStructure::new(
            [apply.clone().into(), design.clone().into()],
            [Edge::results([&design], &apply).unwrap()],
        )
        .unwrap()
        .compile_structure()
        .unwrap(),
    );
    let applied = node_id(&compilation, &[0]);
    let first = Execution::new(Arc::clone(&compilation), ExecutionOptions::new(2).unwrap())
        .run()
        .unwrap();
    let second = Execution::new(Arc::clone(&compilation), ExecutionOptions::new(1).unwrap())
        .run()
        .unwrap();
    assert_eq!(first.status(), ExecutionStatus::Succeeded);
    assert_eq!(*value::<usize>(&first, applied), 2);
    assert_eq!(*value::<usize>(&second, applied), 4);
    assert!(std::ptr::eq(first.compilation(), compilation.as_ref()));
    assert!(std::ptr::eq(first.compilation(), second.compilation()));
}

#[test]
fn graph_owned_ports_and_repeated_mounts_keep_distinct_results() {
    let design = Step::new("design", ActionHandle::new(Design(Arc::default()))).unwrap();
    let producer = Graph::new(
        "producer",
        Arc::new(
            GraphStructure::with_interface(
                [design.clone().into()],
                [],
                GraphInterface::default().output("plan", &design).unwrap(),
            )
            .unwrap(),
        ),
    )
    .unwrap();
    let apply = Step::new("apply", ActionHandle::new(Apply)).unwrap();
    let consumer = Graph::new(
        "consumer",
        Arc::new(
            GraphStructure::with_interface(
                [apply.clone().into()],
                [],
                GraphInterface::default().input("use", [&apply]).unwrap(),
            )
            .unwrap(),
        ),
    )
    .unwrap();
    let shared = Arc::new(
        GraphStructure::new(
            [producer.clone().into(), consumer.clone().into()],
            [Edge::results(
                [producer.interface().output("plan").unwrap()],
                consumer.interface().input("use").unwrap(),
            )
            .unwrap()],
        )
        .unwrap(),
    );
    let root = Graph::new(
        "root",
        Arc::new(
            GraphStructure::new(
                [
                    Graph::new("left", Arc::clone(&shared)).unwrap().into(),
                    Graph::new("right", shared).unwrap().into(),
                ],
                [],
            )
            .unwrap(),
        ),
    )
    .unwrap();
    let compilation = root.compile().unwrap();
    let left = node_id(&compilation, &[0, 0, 1, 0]);
    let right = node_id(&compilation, &[0, 1, 1, 0]);
    let outcome = Execution::new(compilation, ExecutionOptions::new(4).unwrap())
        .run()
        .unwrap();
    let mut results = [
        *value::<usize>(&outcome, left),
        *value::<usize>(&outcome, right),
    ];
    results.sort_unstable();
    assert_eq!(results, [2, 4]);
    assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
}

#[test]
fn explicit_skip_is_missing_input_and_does_not_leak_to_the_next_run() {
    let design = Step::new("design", ActionHandle::new(Design(Arc::default()))).unwrap();
    let apply = Step::new("apply", ActionHandle::new(Apply)).unwrap();
    let compilation = Arc::new(
        GraphStructure::new(
            [design.clone().into(), apply.clone().into()],
            [Edge::results([&design], &apply).unwrap()],
        )
        .unwrap()
        .compile_structure()
        .unwrap(),
    );
    let source = node_id(&compilation, &[0]);
    let target = node_id(&compilation, &[1]);
    let outcome = Execution::new(
        Arc::clone(&compilation),
        ExecutionOptions::new(2).unwrap().skip([source]),
    )
    .run()
    .unwrap();
    assert!(matches!(outcome.node(source), Some(NodeOutcome::Skipped)));
    assert_eq!(*value::<usize>(&outcome, target), 0);
    let outcome = Execution::new(compilation, ExecutionOptions::new(2).unwrap())
        .run()
        .unwrap();
    assert_eq!(*value::<usize>(&outcome, target), 2);
}

struct Unit;
impl Action for Unit {
    type Inputs = ();
    type Output = ();
    fn execute(&self, _: &ActionContext, (): ()) -> Result<(), ActionError> {
        Ok(())
    }
}

struct ReadUnit;
impl Action for ReadUnit {
    type Inputs = (Option<Arc<()>>, Option<Arc<()>>);
    type Output = (bool, bool);
    fn execute(
        &self,
        _: &ActionContext,
        (a, b): Self::Inputs,
    ) -> Result<Self::Output, ActionError> {
        Ok((a.is_some(), b.is_some()))
    }
}

#[test]
fn empty_composite_unit_is_present_but_skipped_composite_is_absent() {
    let present = Graph::new("present", Arc::new(GraphStructure::new([], []).unwrap())).unwrap();
    let absent = Graph::new("absent", Arc::new(GraphStructure::new([], []).unwrap())).unwrap();
    let read = Step::new("read", ActionHandle::new(ReadUnit)).unwrap();
    let compilation = GraphStructure::new(
        [
            present.clone().into(),
            absent.clone().into(),
            read.clone().into(),
        ],
        [Edge::results([&present, &absent], &read).unwrap()],
    )
    .unwrap()
    .compile_structure()
    .unwrap();
    let skipped = node_id(&compilation, &[1]);
    let target = node_id(&compilation, &[2]);
    let outcome = Execution::new(
        compilation,
        ExecutionOptions::new(2).unwrap().skip([skipped]),
    )
    .run()
    .unwrap();
    assert_eq!(*value::<(bool, bool)>(&outcome, target), (true, false));
}

struct Fail;
impl Action for Fail {
    type Inputs = ();
    type Output = ();
    fn execute(&self, _: &ActionContext, (): ()) -> Result<(), ActionError> {
        Err(ActionError::new("original action error"))
    }
}

#[test]
fn failure_blocks_dependents_but_not_an_independent_branch() {
    let failed = Step::new("failed", ActionHandle::new(Fail)).unwrap();
    let blocked = Step::new("blocked", ActionHandle::new(Unit)).unwrap();
    let independent = Step::new("independent", ActionHandle::new(Unit)).unwrap();
    let root = Graph::new(
        "root",
        Arc::new(
            GraphStructure::new(
                [
                    failed.clone().into(),
                    blocked.clone().into(),
                    independent.into(),
                ],
                [Edge::new(&failed, &blocked)],
            )
            .unwrap(),
        ),
    )
    .unwrap();
    let compilation = root.compile().unwrap();
    let root_id = node_id(&compilation, &[0]);
    let failed_id = node_id(&compilation, &[0, 0]);
    let blocked_id = node_id(&compilation, &[0, 1]);
    let independent_id = node_id(&compilation, &[0, 2]);
    // 单 worker 确保失败先被处理，再检查独立分支仍被调度。
    let outcome = Execution::new(compilation, ExecutionOptions::new(1).unwrap())
        .run()
        .unwrap();
    assert_eq!(outcome.status(), ExecutionStatus::Failed);
    let Some(NodeOutcome::Failed(NodeFailure::Action(error))) = outcome.node(failed_id) else {
        panic!("original error was not retained");
    };
    assert_eq!(error.to_string(), "original action error");
    assert!(
        matches!(outcome.node(blocked_id), Some(NodeOutcome::Blocked(BlockCause::Prerequisite(id))) if *id == failed_id)
    );
    assert!(
        matches!(outcome.node(root_id), Some(NodeOutcome::Failed(NodeFailure::Child(id))) if *id == failed_id)
    );
    value::<()>(&outcome, independent_id);
}

#[test]
fn failed_subgraph_does_not_retract_a_successful_exported_result() {
    let design = Step::new("design", ActionHandle::new(Design(Arc::default()))).unwrap();
    let failed = Step::new("failed sibling", ActionHandle::new(Fail)).unwrap();
    let producer = Graph::new(
        "producer",
        Arc::new(
            GraphStructure::with_interface(
                [design.clone().into(), failed.into()],
                [],
                GraphInterface::default().output("plan", &design).unwrap(),
            )
            .unwrap(),
        ),
    )
    .unwrap();
    let apply = Step::new("apply", ActionHandle::new(Apply)).unwrap();
    let compilation = GraphStructure::new(
        [producer.clone().into(), apply.clone().into()],
        [Edge::results([producer.interface().output("plan").unwrap()], &apply).unwrap()],
    )
    .unwrap()
    .compile_structure()
    .unwrap();
    let target = node_id(&compilation, &[1]);
    let outcome = Execution::new(compilation, ExecutionOptions::new(1).unwrap())
        .run()
        .unwrap();
    assert_eq!(outcome.status(), ExecutionStatus::Failed);
    assert_eq!(*value::<usize>(&outcome, target), 2);
}

#[test]
fn invalid_execution_input_is_rejected_before_actions_start() {
    assert!(matches!(
        ExecutionOptions::new(0),
        Err(ExecutionError::InvalidWorkers)
    ));
    let calls = Arc::new(AtomicUsize::new(0));
    let step = Step::new("design", ActionHandle::new(Design(Arc::clone(&calls)))).unwrap();
    let compilation = GraphStructure::new([step.into()], [])
        .unwrap()
        .compile_structure()
        .unwrap();
    let execution = Execution::new(
        compilation,
        ExecutionOptions::new(1).unwrap().skip([NodeId::new(999)]),
    );
    assert!(matches!(execution.run(), Err(ExecutionError::Session(_))));
    assert_eq!(calls.load(Ordering::SeqCst), 0);
}

#[test]
fn empty_and_fully_skipped_graphs_finish_without_action_dispatch() {
    let empty = GraphStructure::new([], [])
        .unwrap()
        .compile_structure()
        .unwrap();
    let outcome = Execution::new(empty, ExecutionOptions::new(1).unwrap())
        .run()
        .unwrap();
    assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
    assert_eq!(outcome.nodes().len(), 0);
    let step = Step::new("must not run", ActionHandle::new(Fail)).unwrap();
    let root = Graph::new(
        "root",
        Arc::new(GraphStructure::new([step.into()], []).unwrap()),
    )
    .unwrap();
    let compilation = root.compile().unwrap();
    let root_id = node_id(&compilation, &[0]);
    let outcome = Execution::new(
        compilation,
        ExecutionOptions::new(1).unwrap().skip([root_id]),
    )
    .run()
    .unwrap();
    assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
    assert!(
        outcome
            .nodes()
            .all(|(_, node)| matches!(node, NodeOutcome::Skipped))
    );
}

#[derive(Default)]
struct Concurrency {
    started: Mutex<usize>,
    ready: Condvar,
    active: AtomicUsize,
    maximum: AtomicUsize,
}

struct ConcurrentWork(Arc<Concurrency>);
impl Action for ConcurrentWork {
    type Inputs = ();
    type Output = ();
    fn execute(&self, _: &ActionContext, (): ()) -> Result<(), ActionError> {
        let active = self.0.active.fetch_add(1, Ordering::SeqCst) + 1;
        self.0.maximum.fetch_max(active, Ordering::SeqCst);
        let mut started = self.0.started.lock().unwrap();
        *started += 1;
        self.0.ready.notify_all();
        let (started, timeout) = self
            .0
            .ready
            .wait_timeout_while(started, Duration::from_secs(3), |started| *started < 2)
            .unwrap();
        drop(started);
        std::thread::sleep(Duration::from_millis(5));
        self.0.active.fetch_sub(1, Ordering::SeqCst);
        if timeout.timed_out() {
            return Err(ActionError::new("second worker was not dispatched"));
        }
        Ok(())
    }
}

#[test]
fn workers_bound_the_whole_run_across_multiple_completion_batches() {
    let concurrency = Arc::new(Concurrency::default());
    let leaves = (0..12).map(|index| {
        Step::new(
            index.to_string(),
            ActionHandle::new(ConcurrentWork(Arc::clone(&concurrency))),
        )
        .unwrap()
        .into()
    });
    let compilation = GraphStructure::new(leaves, [])
        .unwrap()
        .compile_structure()
        .unwrap();
    let outcome = Execution::new(compilation, ExecutionOptions::new(2).unwrap())
        .run()
        .unwrap();
    assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
    assert_eq!(concurrency.maximum.load(Ordering::SeqCst), 2);
    assert_eq!(*concurrency.started.lock().unwrap(), 12);
    assert_eq!(concurrency.active.load(Ordering::SeqCst), 0);
}
