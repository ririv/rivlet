use action::{Action, ActionContext, ActionError, ActionHandle};
use execution::{CancellationSource, Execution, ExecutionOptions, ResultLookupError};
use graph::{
    Condition, Edge, Graph, GraphInterface, LookupError, NodeKind, PortName, StaticRule, Step,
};
use std::sync::Arc;

struct Number;
impl Action for Number {
    type Inputs = ();
    type Output = usize;
    fn execute(&self, _: &ActionContext, (): ()) -> Result<usize, ActionError> {
        Ok(42)
    }
}
struct Fail;
impl Action for Fail {
    type Inputs = ();
    type Output = ();
    fn execute(&self, _: &ActionContext, (): ()) -> Result<(), ActionError> {
        Err(ActionError::new("original failure"))
    }
}
fn number() -> Step {
    Step::new("number", ActionHandle::new(Number)).unwrap()
}
fn run(graph: &Graph) -> execution::ExecutionOutcome {
    Execution::new(graph.compile().unwrap(), ExecutionOptions::new(2).unwrap())
        .run()
        .unwrap()
}

#[test]
fn node_anonymous_named_and_forwarded_ports_query_the_same_original_value() {
    let value = number();
    let child = Graph::with_interface(
        "child",
        [value.clone().into()],
        [],
        GraphInterface::default()
            .output(PortName::Anonymous, &value)
            .unwrap()
            .output("number", &value)
            .unwrap(),
    )
    .unwrap();
    let root = Graph::with_interface(
        "root",
        [child.clone().into()],
        [],
        GraphInterface::default()
            .output("answer", child.interface().output("number").unwrap())
            .unwrap(),
    )
    .unwrap();
    let outcome = run(&root);
    let direct = outcome.result::<usize>(&value).unwrap().unwrap();
    for port in [
        child.interface().output(PortName::Anonymous).unwrap(),
        root.interface().output("answer").unwrap(),
    ] {
        assert!(Arc::ptr_eq(
            &direct,
            &outcome.result::<usize>(port).unwrap().unwrap()
        ));
    }
    assert_eq!(*direct, 42);
    assert!(matches!(
        outcome.result::<String>(&value),
        Err(ResultLookupError::Value { .. })
    ));
    assert!(outcome.result::<()>(&root).unwrap().is_some());
}

#[test]
fn repeated_declarations_are_ambiguous_but_unique_graph_ports_resolve_actual_mounts() {
    let value = number();
    let mount = |label| {
        Graph::with_interface(
            label,
            [value.clone().into()],
            [],
            GraphInterface::default().output("answer", &value).unwrap(),
        )
        .unwrap()
    };
    let left = mount("left");
    let right = mount("right");
    let outcome =
        run(&Graph::new("root", [left.clone().into(), right.clone().into()], []).unwrap());
    assert!(
        matches!(outcome.result::<usize>(&value), Err(ResultLookupError::Reference(LookupError::AmbiguousNode(id))) if id == value.id())
    );
    let a = outcome
        .result::<usize>(left.interface().output("answer").unwrap())
        .unwrap()
        .unwrap();
    let b = outcome
        .result::<usize>(right.interface().output("answer").unwrap())
        .unwrap()
        .unwrap();
    assert_eq!((*a, *b), (42, 42));
    assert!(!Arc::ptr_eq(&a, &b));
    let repeated = run(&Graph::new(
        "root",
        [
            left.clone().into(),
            Graph::new("wrapper", [left.clone().into()], [])
                .unwrap()
                .into(),
        ],
        [],
    )
    .unwrap());
    assert!(matches!(
        repeated.result::<usize>(left.interface().output("answer").unwrap()),
        Err(ResultLookupError::Reference(LookupError::AmbiguousNode(_)))
    ));
}

#[test]
fn pruning_is_not_skipping_and_compiled_indexes_do_not_use_original_path_positions() {
    let pruned = number();
    let active = number();
    let root = Graph::with_interface(
        "root",
        [pruned.clone().into(), active.clone().into()],
        [],
        GraphInterface::default().output("gone", &pruned).unwrap(),
    )
    .unwrap()
    .with_static([StaticRule::when(false, &pruned)]);
    let outcome = run(&root);
    assert_eq!(*outcome.result::<usize>(&active).unwrap().unwrap(), 42);
    assert_eq!(
        outcome
            .compilation()
            .node(outcome.compilation().node_index(&active).unwrap())
            .path(),
        &[0, 1]
    );
    assert!(matches!(
        outcome.result::<usize>(&pruned),
        Err(ResultLookupError::Reference(LookupError::NodeUnavailable(
            _
        )))
    ));
    assert!(matches!(
        outcome.result::<usize>(root.interface().output("gone").unwrap()),
        Err(ResultLookupError::Reference(
            LookupError::PortUnavailable { .. }
        ))
    ));
    assert!(matches!(
        outcome.result::<usize>(&number()),
        Err(ResultLookupError::Reference(LookupError::NodeUnavailable(
            _
        )))
    ));
}

#[test]
fn only_skipped_results_are_none_and_failure_blockage_cancellation_remain_errors() {
    let skipped = Step::with_condition(
        "skipped",
        ActionHandle::new(Number),
        Condition::new(|()| Ok(false), []).unwrap(),
    )
    .unwrap();
    let fail = Step::new("fail", ActionHandle::new(Fail)).unwrap();
    let blocked = number();
    let root = Graph::new(
        "root",
        [
            skipped.clone().into(),
            fail.clone().into(),
            blocked.clone().into(),
        ],
        [Edge::new(&fail, &blocked)],
    )
    .unwrap();
    let outcome = run(&root);
    assert!(outcome.result::<usize>(&skipped).unwrap().is_none());
    assert!(matches!(
        outcome.result::<()>(&fail),
        Err(ResultLookupError::Failed { .. })
    ));
    assert!(matches!(
        outcome.result::<usize>(&blocked),
        Err(ResultLookupError::Blocked { .. })
    ));
    let source = CancellationSource::new();
    source.cancel();
    let cancelled = Execution::new(
        blocked.compile().unwrap(),
        ExecutionOptions::new(1)
            .unwrap()
            .cancellation(source.token()),
    )
    .run()
    .unwrap();
    assert!(matches!(
        cancelled.result::<usize>(&blocked),
        Err(ResultLookupError::Cancelled(_))
    ));
}

#[test]
fn step_and_closed_node_union_compile_as_actual_roots_without_a_synthetic_graph_node() {
    let value = number();
    for compilation in [
        value.compile().unwrap(),
        NodeKind::from(value.clone()).compile().unwrap(),
    ] {
        assert_eq!(compilation.graph().nodes().len(), 1);
        assert_eq!(
            compilation
                .node(compilation.node_index(&value).unwrap())
                .path(),
            &[0]
        );
        let outcome = Execution::new(compilation, ExecutionOptions::new(1).unwrap())
            .run()
            .unwrap();
        assert_eq!(*outcome.result::<usize>(&value).unwrap().unwrap(), 42);
    }
}
