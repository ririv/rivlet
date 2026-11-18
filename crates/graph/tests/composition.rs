use std::sync::{Arc, Mutex};

use rivlet_action::{Action, ActionContext, ActionError, ActionHandle};
use rivlet_core::{ExecutionSession, ExecutionUpdate, NodeState, SessionStatus};
use rivlet_graph::{
    Compilation, Edge, Graph, GraphError, GraphInterface, Node, PortName, ProjectedKind, Step,
};

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
    let body = Arc::new(Graph::new("scope", [task.clone().into()], []).unwrap());
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
    assert_ne!(left.id(), right.id());
    assert_eq!(left.nodes()[0].id(), right.nodes()[0].id());
    let structure = Graph::new(
        "scope",
        [left.clone().into(), right.clone().into()],
        [Edge::new(&left, &right)],
    )
    .unwrap();
    let root = Graph::with_interface(
        "root",
        structure.nodes().iter().cloned(),
        structure.edges().iter().cloned(),
        structure.interface().declaration().clone(),
    )
    .unwrap();
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
fn anonymous_and_named_ports_coexist_without_name_inference() {
    let events = Arc::new(Mutex::new(Vec::new()));
    let ready = step("ready", &events);
    let report = step("report", &events);
    let producer = Graph::with_interface(
        "producer",
        [ready.clone().into(), report.clone().into()],
        [Edge::new(&ready, &report)],
        GraphInterface::default()
            .output(PortName::Anonymous, &ready)
            .unwrap()
            .output("default", &report)
            .unwrap(),
    )
    .unwrap();
    let first = step("first", &events);
    let second = step("second", &events);
    let consumer = Graph::with_interface(
        "consumer",
        [first.clone().into(), second.clone().into()],
        [],
        GraphInterface::default()
            .input(PortName::Anonymous, [&first, &second])
            .unwrap()
            .input("named", [&first])
            .unwrap(),
    )
    .unwrap();
    let anonymous = producer.interface().output(PortName::Anonymous).unwrap();
    assert_eq!(anonymous.name(), &PortName::Anonymous);
    assert_ne!(anonymous, producer.interface().output("default").unwrap());
    let root = Graph::new(
        "scope",
        [producer.into(), consumer.clone().into()],
        [Edge::ports(
            anonymous,
            consumer.interface().input(PortName::Anonymous).unwrap(),
        )],
    )
    .unwrap()
    .compile()
    .unwrap();
    assert_eq!(root.projection().port_connections().len(), 2);
    assert_eq!(run(&root).0, SessionStatus::Finished);
    let events = events.lock().unwrap();
    assert_eq!(events.len(), 4);
    assert_eq!(events[0], "ready");

    let named_only = Graph::with_interface(
        "named-only",
        [first.clone().into()],
        [],
        GraphInterface::default().output("default", &first).unwrap(),
    )
    .unwrap();
    assert!(matches!(
        named_only.interface().output(PortName::Anonymous),
        Err(GraphError::UnknownOutputPort(PortName::Anonymous))
    ));
    assert!(matches!(
        GraphInterface::default()
            .output(PortName::Anonymous, &first)
            .unwrap()
            .output(PortName::Anonymous, &second),
        Err(GraphError::DuplicatePort(PortName::Anonymous))
    ));
}

#[test]
fn forwarded_ports_allow_interleaving_without_false_component_cycle() {
    let events = Arc::new(Mutex::new(Vec::new()));
    let start = step("start", &events);
    let finish = step("finish", &events);
    let first_body = Arc::new(
        Graph::with_interface(
            "scope",
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
    let first = Graph::with_interface(
        "first",
        first_body.nodes().iter().cloned(),
        first_body.edges().iter().cloned(),
        first_body.interface().declaration().clone(),
    )
    .unwrap();
    let wrapper_body = Arc::new(
        Graph::with_interface(
            "scope",
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
    let wrapper = Graph::with_interface(
        "wrapper",
        wrapper_body.nodes().iter().cloned(),
        wrapper_body.edges().iter().cloned(),
        wrapper_body.interface().declaration().clone(),
    )
    .unwrap();
    let middle = step("middle", &events);
    let second = Graph::with_interface(
        "second",
        [middle.clone().into()],
        [],
        GraphInterface::default()
            .input("start", [&middle])
            .unwrap()
            .output("done", &middle)
            .unwrap(),
    )
    .unwrap();
    let structure = Graph::new(
        "scope",
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
        run(&structure.compile().unwrap()).0,
        SessionStatus::Finished
    );
    assert_eq!(*events.lock().unwrap(), ["start", "middle", "finish"]);
}

#[test]
fn failed_output_blocks_only_its_consumers_and_not_independent_work() {
    let events = Arc::new(Mutex::new(Vec::new()));
    let fail = step_with_outcome("failed", &events, Err(ActionError::new("failure")));
    let provider = Graph::with_interface(
        "provider",
        [fail.clone().into()],
        [],
        GraphInterface::default().output("ready", &fail).unwrap(),
    )
    .unwrap();
    let blocked = step("blocked", &events);
    let consumer = Graph::with_interface(
        "consumer",
        [blocked.clone().into()],
        [],
        GraphInterface::default()
            .input("apply", [&blocked])
            .unwrap(),
    )
    .unwrap();
    let independent = step("independent", &events);
    let structure = Graph::new(
        "scope",
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
    let compilation = structure.compile().unwrap();
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
            let id = rivlet_core::NodeId::new(index);
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
    let consumer = Graph::with_interface(
        "consumer",
        [first.clone().into(), second.clone().into()],
        [],
        GraphInterface::default()
            .input("apply", [&first, &second])
            .unwrap()
            .output("done", &second)
            .unwrap(),
    )
    .unwrap();
    let provider = Graph::with_interface(
        "provider",
        [ready.clone().into(), later_fail.clone().into()],
        [],
        GraphInterface::default()
            .input("late", [&later_fail])
            .unwrap()
            .output("ready", &ready)
            .unwrap(),
    )
    .unwrap();
    let structure = Graph::new(
        "scope",
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
    assert_eq!(run(&structure.compile().unwrap()).0, SessionStatus::Failed);
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
        Graph::with_interface(
            "scope",
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
    let a = Graph::with_interface(
        "a",
        body.nodes().iter().cloned(),
        body.edges().iter().cloned(),
        body.interface().declaration().clone(),
    )
    .unwrap();
    let b = Graph::with_interface(
        "b",
        body.nodes().iter().cloned(),
        body.edges().iter().cloned(),
        body.interface().declaration().clone(),
    )
    .unwrap();
    let structure = Graph::new(
        "scope",
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
        structure.compile(),
        Err(GraphError::Declaration(_))
    ));
    assert!(events.lock().unwrap().is_empty());
}

#[test]
fn empty_graph_and_clone_membership_have_explicit_semantics() {
    let graph = Graph::new("empty", [], []).unwrap();
    assert_eq!(run(&graph.compile().unwrap()).0, SessionStatus::Finished);
    assert!(matches!(
        Graph::new("scope", [graph.clone().into(), graph.clone().into()], []),
        Err(GraphError::DuplicateNode(_))
    ));
    assert!(matches!(
        graph.interface().output("missing"),
        Err(GraphError::UnknownOutputPort(_))
    ));
}
