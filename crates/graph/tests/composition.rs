use std::sync::{Arc, Mutex};

use action::{Action, ActionContext, ActionError, ActionHandle};
use graph::{
    Compilation, Edge, Graph, GraphError, GraphInterface, GraphStructure, ProjectedKind, Step,
};
use rivlet::{ExecutionSession, ExecutionUpdate, NodeState, SessionStatus};

// 使用普通 Rust 函数对象，不依赖 shell、Python 或独立调度器。
struct Record {
    name: &'static str,
    events: Arc<Mutex<Vec<&'static str>>>,
    outcome: Result<(), ActionError>,
}

impl Action for Record {
    type Inputs = ();
    type Output = ();
    fn execute(&self, _: &ActionContext, (): ()) -> Result<(), ActionError> {
        self.events.lock().unwrap().push(self.name);
        self.outcome.clone()
    }
}

fn step(name: &'static str, events: &Arc<Mutex<Vec<&'static str>>>) -> Step {
    step_with_outcome(name, events, Ok(()))
}

fn step_with_outcome(
    name: &'static str,
    events: &Arc<Mutex<Vec<&'static str>>>,
    outcome: Result<(), ActionError>,
) -> Step {
    Step::new(
        name,
        ActionHandle::new(Record {
            name,
            events: Arc::clone(events),
            outcome,
        }),
    )
    .unwrap()
}

fn run(compilation: &Compilation) -> (SessionStatus, ExecutionSession) {
    let mut session = ExecutionSession::new(Arc::new(compilation.graph().clone()), []).unwrap();
    let mut batch = session.start(1).unwrap();
    while batch.status() == SessionStatus::Running {
        assert!(batch.conditions().is_empty());
        assert!(!batch.actions().is_empty(), "unexpected stalled session");
        let completed = batch
            .actions()
            .iter()
            .map(|id| {
                let ProjectedKind::Step(action) = compilation.node(*id).kind() else {
                    panic!("a composite was dispatched as an action");
                };
                (
                    *id,
                    action
                        .execute(&ActionContext::default(), &[])
                        .map(|_| ())
                        .map_err(|_| ()),
                )
            })
            .collect::<Vec<_>>();
        batch = session
            .advance(ExecutionUpdate::new(completed, [], []), 1)
            .unwrap();
    }
    (batch.status(), session)
}

#[test]
fn nested_shared_structure_preserves_identity_paths_and_real_composites() {
    let events = Arc::new(Mutex::new(Vec::new()));
    let task = step("task", &events);
    let body = Arc::new(GraphStructure::new([task.clone().into()], []).unwrap());
    let left = Graph::new("left", Arc::clone(&body)).unwrap();
    let right = Graph::new("right", Arc::clone(&body)).unwrap();
    assert_ne!(left.id(), right.id());
    assert!(Arc::ptr_eq(left.structure(), right.structure()));
    let structure = GraphStructure::new(
        [left.clone().into(), right.clone().into()],
        [Edge::new(&left, &right)],
    )
    .unwrap();
    let root = Graph::new("root", Arc::new(structure)).unwrap();
    let compilation = root.compile().unwrap();
    let nodes = compilation.projection().nodes();
    assert_eq!(nodes.len(), 5);
    assert_eq!(nodes[0].identity(), root.id());
    assert!(matches!(nodes[0].kind(), ProjectedKind::Graph(_)));
    assert_eq!(nodes[2].identity(), task.id());
    assert_eq!(nodes[4].identity(), task.id());
    assert_eq!(nodes[2].path(), &[0, 0, 0]);
    assert_eq!(nodes[4].path(), &[0, 1, 0]);
    assert_eq!(run(&compilation).0, SessionStatus::Finished);
    assert_eq!(*events.lock().unwrap(), ["task", "task"]);
}

#[test]
fn forwarded_ports_allow_interleaving_without_false_component_cycle() {
    let events = Arc::new(Mutex::new(Vec::new()));
    let start = step("start", &events);
    let finish = step("finish", &events);
    let first_body = Arc::new(
        GraphStructure::with_interface(
            [start.clone().into(), finish.clone().into()],
            [],
            GraphInterface::default()
                .input("finish", [&finish])
                .unwrap()
                .output("started", &start)
                .unwrap(),
        )
        .unwrap(),
    );
    let first = Graph::new("first", first_body).unwrap();
    let wrapper_body = Arc::new(
        GraphStructure::with_interface(
            [first.clone().into()],
            [],
            GraphInterface::default()
                .input("apply", [first.interface().input("finish").unwrap()])
                .unwrap()
                .output("ready", first.interface().output("started").unwrap())
                .unwrap(),
        )
        .unwrap(),
    );
    let wrapper = Graph::new("wrapper", wrapper_body).unwrap();
    let middle = step("middle", &events);
    let second = Graph::new(
        "second",
        Arc::new(
            GraphStructure::with_interface(
                [middle.clone().into()],
                [],
                GraphInterface::default()
                    .input("start", [&middle])
                    .unwrap()
                    .output("done", &middle)
                    .unwrap(),
            )
            .unwrap(),
        ),
    )
    .unwrap();
    let structure = GraphStructure::new(
        [wrapper.clone().into(), second.clone().into()],
        [
            Edge::ports(
                wrapper.interface().output("ready").unwrap(),
                second.interface().input("start").unwrap(),
            ),
            Edge::ports(
                second.interface().output("done").unwrap(),
                wrapper.interface().input("apply").unwrap(),
            ),
        ],
    )
    .unwrap();
    assert_eq!(
        run(&structure.compile_structure().unwrap()).0,
        SessionStatus::Finished
    );
    assert_eq!(*events.lock().unwrap(), ["start", "middle", "finish"]);
}

#[test]
fn failed_output_blocks_only_its_consumers_and_not_independent_work() {
    let events = Arc::new(Mutex::new(Vec::new()));
    let fail = step_with_outcome("failed", &events, Err(ActionError::new("failure")));
    let provider = Graph::new(
        "provider",
        Arc::new(
            GraphStructure::with_interface(
                [fail.clone().into()],
                [],
                GraphInterface::default().output("ready", &fail).unwrap(),
            )
            .unwrap(),
        ),
    )
    .unwrap();
    let blocked = step("blocked", &events);
    let consumer = Graph::new(
        "consumer",
        Arc::new(
            GraphStructure::with_interface(
                [blocked.clone().into()],
                [],
                GraphInterface::default()
                    .input("apply", [&blocked])
                    .unwrap(),
            )
            .unwrap(),
        ),
    )
    .unwrap();
    let independent = step("independent", &events);
    let structure = GraphStructure::new(
        [
            provider.clone().into(),
            consumer.clone().into(),
            independent.into(),
        ],
        [Edge::ports(
            provider.interface().output("ready").unwrap(),
            consumer.interface().input("apply").unwrap(),
        )],
    )
    .unwrap();
    let compilation = structure.compile_structure().unwrap();
    let (status, session) = run(&compilation);
    assert_eq!(status, SessionStatus::Failed);
    let mut actual = events.lock().unwrap().clone();
    actual.sort_unstable();
    assert_eq!(actual, ["failed", "independent"]);
    let target = compilation
        .graph()
        .nodes()
        .iter()
        .enumerate()
        .find_map(|(index, _)| {
            let id = rivlet::NodeId::new(index);
            (compilation.node(id).identity() == blocked.id()).then_some(id)
        })
        .unwrap();
    assert_eq!(session.state(target), Some(NodeState::Blocked));
}

#[test]
fn port_fanout_does_not_wait_for_unrelated_provider_completion() {
    let events = Arc::new(Mutex::new(Vec::new()));
    let ready = step("ready", &events);
    let later_fail = step_with_outcome("later-fail", &events, Err(ActionError::new("late")));
    let first = step("first", &events);
    let second = step("second", &events);
    let consumer = Graph::new(
        "consumer",
        Arc::new(
            GraphStructure::with_interface(
                [first.clone().into(), second.clone().into()],
                [],
                GraphInterface::default()
                    .input("apply", [&first, &second])
                    .unwrap()
                    .output("done", &second)
                    .unwrap(),
            )
            .unwrap(),
        ),
    )
    .unwrap();
    let provider = Graph::new(
        "provider",
        Arc::new(
            GraphStructure::with_interface(
                [ready.clone().into(), later_fail.clone().into()],
                [],
                GraphInterface::default()
                    .input("late", [&later_fail])
                    .unwrap()
                    .output("ready", &ready)
                    .unwrap(),
            )
            .unwrap(),
        ),
    )
    .unwrap();
    let structure = GraphStructure::new(
        [provider.clone().into(), consumer.clone().into()],
        [
            Edge::ports(
                provider.interface().output("ready").unwrap(),
                consumer.interface().input("apply").unwrap(),
            ),
            Edge::ports(
                consumer.interface().output("done").unwrap(),
                provider.interface().input("late").unwrap(),
            ),
        ],
    )
    .unwrap();
    assert_eq!(
        run(&structure.compile_structure().unwrap()).0,
        SessionStatus::Failed
    );
    assert_eq!(
        *events.lock().unwrap(),
        ["ready", "first", "second", "later-fail"]
    );
}

#[test]
fn resolved_port_cycle_is_rejected_by_the_common_compiler() {
    let events = Arc::new(Mutex::new(Vec::new()));
    let task = step("task", &events);
    let body = Arc::new(
        GraphStructure::with_interface(
            [task.clone().into()],
            [],
            GraphInterface::default()
                .input("in", [&task])
                .unwrap()
                .output("out", &task)
                .unwrap(),
        )
        .unwrap(),
    );
    let a = Graph::new("a", Arc::clone(&body)).unwrap();
    let b = Graph::new("b", body).unwrap();
    let structure = GraphStructure::new(
        [a.clone().into(), b.clone().into()],
        [
            Edge::ports(
                a.interface().output("out").unwrap(),
                b.interface().input("in").unwrap(),
            ),
            Edge::ports(
                b.interface().output("out").unwrap(),
                a.interface().input("in").unwrap(),
            ),
        ],
    )
    .unwrap();
    assert!(matches!(
        structure.compile_structure(),
        Err(GraphError::Declaration(_))
    ));
    assert!(events.lock().unwrap().is_empty());
}

#[test]
fn empty_graph_and_clone_membership_have_explicit_semantics() {
    let graph = Graph::new("empty", Arc::new(GraphStructure::new([], []).unwrap())).unwrap();
    assert_eq!(run(&graph.compile().unwrap()).0, SessionStatus::Finished);
    assert!(matches!(
        GraphStructure::new([graph.clone().into(), graph.clone().into()], []),
        Err(GraphError::DuplicateNode(_))
    ));
    assert!(matches!(
        graph.interface().output("missing"),
        Err(GraphError::UnknownOutputPort(_))
    ));
}
