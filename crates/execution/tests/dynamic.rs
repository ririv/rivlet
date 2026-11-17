use action::{Action, ActionContext, ActionError, ActionHandle};
use execution::{
    CancellationSource, Execution, ExecutionOptions, ExecutionOutcome, ExecutionStatus,
    NodeEventDetail, NodeFailure, NodeOutcome,
};
use graph::{Case, Condition, Edge, Graph, StaticRule, Step};
use rivlet::NodeId;
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicUsize, Ordering},
};
use std::time::Duration;

struct Generate(Graph);
impl Action for Generate {
    type Inputs = ();
    type Output = Graph;
    fn execute(&self, _: &ActionContext, (): ()) -> Result<Graph, ActionError> {
        Ok(self.0.clone())
    }
}
struct Record {
    calls: Arc<Mutex<Vec<&'static str>>>,
    label: &'static str,
    fail: bool,
}
impl Action for Record {
    type Inputs = ();
    type Output = usize;
    fn execute(&self, _: &ActionContext, (): ()) -> Result<usize, ActionError> {
        self.calls.lock().unwrap().push(self.label);
        if self.fail {
            Err(ActionError::new("child failure"))
        } else {
            Ok(42)
        }
    }
}
fn record(calls: &Arc<Mutex<Vec<&'static str>>>, label: &'static str, fail: bool) -> Step {
    Step::new(
        label,
        ActionHandle::new(Record {
            calls: Arc::clone(calls),
            label,
            fail,
        }),
    )
    .unwrap()
}
fn id(outcome: &ExecutionOutcome, path: &[usize]) -> NodeId {
    outcome
        .nodes()
        .map(|(id, _)| id)
        .find(|id| outcome.compilation().node(*id).path() == path)
        .unwrap()
}

#[test]
fn nested_generated_graphs_complete_before_the_parent_and_retain_typed_results() {
    let calls = Arc::default();
    let inner = record(&calls, "inner", false);
    let nested = Step::new(
        "nested",
        ActionHandle::new(Generate(
            Graph::new("inner graph", [inner.into()], []).unwrap(),
        )),
    )
    .unwrap();
    let generated = Graph::new("generated", [nested.into()], []).unwrap();
    let factory = Step::new("factory", ActionHandle::new(Generate(generated.clone()))).unwrap();
    let tail = record(&calls, "tail", false);
    let root = Graph::new(
        "root",
        [factory.clone().into(), tail.clone().into()],
        [Edge::new(&factory, &tail)],
    )
    .unwrap();
    let events = Arc::new(Mutex::new(Vec::new()));
    let observed = Arc::clone(&events);
    let outcome = Execution::new(
        root.compile().unwrap(),
        ExecutionOptions::new(1).unwrap().events(move |event| {
            observed.lock().unwrap().push(event.clone());
            Ok(())
        }),
    )
    .run()
    .unwrap();
    assert_eq!(*calls.lock().unwrap(), ["inner", "tail"]);
    let factory = id(&outcome, &[0, 0]);
    let Some(NodeOutcome::Succeeded(value)) = outcome.node(factory) else {
        panic!("missing graph");
    };
    assert_eq!(value.downcast::<Graph>().unwrap().id(), generated.id());
    let child = outcome.subgraph(factory).unwrap();
    let nested = id(child, &[0, 0]);
    assert_eq!(
        child.subgraph(nested).unwrap().status(),
        ExecutionStatus::Succeeded
    );
    let events = events.lock().unwrap();
    assert!(
        events
            .iter()
            .any(|event| event.node_path == [0, 0, 0, 0, 0, 0]
                && matches!(event.state_detail, NodeEventDetail::Completed))
    );
    assert!(
        events
            .iter()
            .enumerate()
            .all(|(index, event)| event.sequence == index as u64)
    );
}

#[test]
fn failed_generated_graph_blocks_only_dependents_and_preserves_child_outcome() {
    let calls = Arc::default();
    let fail = record(&calls, "fail", true);
    let factory = Step::new(
        "factory",
        ActionHandle::new(Generate(Graph::new("child", [fail.into()], []).unwrap())),
    )
    .unwrap();
    let blocked = record(&calls, "blocked", false);
    let other = record(&calls, "other", false);
    let root = Graph::new(
        "root",
        [factory.clone().into(), blocked.clone().into(), other.into()],
        [Edge::new(&factory, &blocked)],
    )
    .unwrap();
    let outcome = Execution::new(root.compile().unwrap(), ExecutionOptions::new(2).unwrap())
        .run()
        .unwrap();
    let factory = id(&outcome, &[0, 0]);
    assert!(matches!(
        outcome.node(factory),
        Some(NodeOutcome::Failed(NodeFailure::Subgraph))
    ));
    assert_eq!(
        outcome.subgraph(factory).unwrap().status(),
        ExecutionStatus::Failed
    );
    assert!(calls.lock().unwrap().contains(&"other"));
    assert!(!calls.lock().unwrap().contains(&"blocked"));
}

struct Busy {
    active: Arc<AtomicUsize>,
    peak: Arc<AtomicUsize>,
}
impl Action for Busy {
    type Inputs = ();
    type Output = ();
    fn execute(&self, _: &ActionContext, (): ()) -> Result<(), ActionError> {
        let active = self.active.fetch_add(1, Ordering::SeqCst) + 1;
        self.peak.fetch_max(active, Ordering::SeqCst);
        std::thread::sleep(Duration::from_millis(2));
        self.active.fetch_sub(1, Ordering::SeqCst);
        Ok(())
    }
}
#[test]
fn all_generated_sessions_share_one_worker_budget() {
    let active = Arc::new(AtomicUsize::new(0));
    let peak = Arc::new(AtomicUsize::new(0));
    let generators = (0..4)
        .map(|_| {
            let children = (0..4)
                .map(|_| {
                    Step::new(
                        "busy",
                        ActionHandle::new(Busy {
                            active: Arc::clone(&active),
                            peak: Arc::clone(&peak),
                        }),
                    )
                    .unwrap()
                    .into()
                })
                .collect::<Vec<_>>();
            Step::new(
                "generate",
                ActionHandle::new(Generate(Graph::new("child", children, []).unwrap())),
            )
            .unwrap()
            .into()
        })
        .collect::<Vec<_>>();
    let root = Graph::new("root", generators, []).unwrap();
    for workers in [1, 2] {
        peak.store(0, Ordering::SeqCst);
        let outcome = Execution::new(
            root.compile().unwrap(),
            ExecutionOptions::new(workers).unwrap(),
        )
        .run()
        .unwrap();
        assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
        assert_eq!(peak.load(Ordering::SeqCst), workers);
    }
}

struct Cancel(CancellationSource);
impl Action for Cancel {
    type Inputs = ();
    type Output = ();
    fn execute(&self, ctx: &ActionContext, (): ()) -> Result<(), ActionError> {
        self.0.cancel();
        ctx.control().require_running()
    }
}
#[test]
fn cancellation_reaches_generated_graphs_without_publishing_the_generator_result() {
    let source = CancellationSource::new();
    let work = Step::new("cancel", ActionHandle::new(Cancel(source.clone()))).unwrap();
    let factory = Step::new(
        "generate",
        ActionHandle::new(Generate(Graph::new("child", [work.into()], []).unwrap())),
    )
    .unwrap();
    let root = Graph::new("root", [factory.into()], []).unwrap();
    let outcome = Execution::new(
        root.compile().unwrap(),
        ExecutionOptions::new(1)
            .unwrap()
            .cancellation(source.token()),
    )
    .run()
    .unwrap();
    let factory = id(&outcome, &[0, 0]);
    assert!(matches!(
        outcome.node(factory),
        Some(NodeOutcome::Cancelled)
    ));
    assert_eq!(
        outcome.subgraph(factory).unwrap().status(),
        ExecutionStatus::Cancelled
    );
}

#[test]
fn compilation_errors_are_expansion_failures_and_async_predicates_require_async_entry() {
    let child = Graph::new("bad", [], [])
        .unwrap()
        .with_static([StaticRule::select(Vec::<Case>::new())]);
    let factory = Step::new("generate", ActionHandle::new(Generate(child))).unwrap();
    let outcome = Execution::new(
        Graph::new("root", [factory.into()], [])
            .unwrap()
            .compile()
            .unwrap(),
        ExecutionOptions::new(1).unwrap(),
    )
    .run()
    .unwrap();
    assert!(matches!(
        outcome.node(id(&outcome, &[0, 0])),
        Some(NodeOutcome::Failed(NodeFailure::Expansion(_)))
    ));

    let calls = Arc::default();
    let work = record(&calls, "work", false);
    let child = Graph::with_condition(
        "async child",
        [work.into()],
        [],
        graph::GraphInterface::default(),
        Condition::new_async(|()| async { Ok(true) }, []).unwrap(),
    )
    .unwrap();
    let root = Graph::new(
        "root",
        [Step::new("generate", ActionHandle::new(Generate(child)))
            .unwrap()
            .into()],
        [],
    )
    .unwrap();
    let sync = Execution::new(root.compile().unwrap(), ExecutionOptions::new(1).unwrap())
        .run()
        .unwrap();
    assert_eq!(sync.status(), ExecutionStatus::Failed);
    assert!(matches!(
        sync.node(id(&sync, &[0, 0])),
        Some(NodeOutcome::Failed(NodeFailure::AsyncSubgraph))
    ));
    assert!(calls.lock().unwrap().is_empty());
    let runtime = tokio::runtime::Builder::new_current_thread()
        .build()
        .unwrap();
    let outcome = runtime
        .block_on(
            Execution::new(root.compile().unwrap(), ExecutionOptions::new(1).unwrap()).run_async(),
        )
        .unwrap();
    assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
    assert_eq!(*calls.lock().unwrap(), ["work"]);
}
