use action::{Action, ActionContext, ActionError, ActionHandle};
use graph::{
    Case, Edge, Graph, GraphError, GraphInterface, StaticCondition, StaticError, StaticRule, Step,
};
use std::sync::{
    Arc,
    atomic::{AtomicBool, AtomicUsize, Ordering},
};

struct Work;
impl Action for Work {
    type Inputs = ();
    type Output = ();
    fn execute(&self, _: &ActionContext, (): ()) -> Result<(), ActionError> {
        Ok(())
    }
}
fn step(label: &str) -> Step {
    Step::new(label, ActionHandle::new(Work)).unwrap()
}

#[test]
fn selections_preserve_declaration_and_re_evaluate_only_for_fresh_compilation() {
    let enabled = Arc::new(AtomicBool::new(true));
    let a = step("a");
    let b = step("b");
    let yes = Arc::clone(&enabled);
    let no = Arc::clone(&enabled);
    let graph = Graph::new("root", [a.clone().into(), b.clone().into()], [])
        .unwrap()
        .with_static([StaticRule::select([
            Case::new(
                StaticCondition::evaluate(move || yes.load(Ordering::SeqCst)),
                &a,
            ),
            Case::new(
                StaticCondition::evaluate(move || !no.load(Ordering::SeqCst)),
                &b,
            ),
        ])]);
    let first = graph.compile().unwrap();
    enabled.store(false, Ordering::SeqCst);
    let second = graph.compile().unwrap();
    assert_eq!(graph.nodes().len(), 2);
    assert_eq!(first.graph().nodes().len(), 2);
    assert_eq!(first.projection().nodes()[1].identity(), a.id());
    assert_eq!(second.projection().nodes()[1].identity(), b.id());
    assert_eq!(second.projection().nodes()[1].path(), &[0, 1]);
}

#[test]
fn inactive_children_are_not_evaluated_and_edges_do_not_delete_nodes() {
    let calls = Arc::new(AtomicUsize::new(0));
    let observed = Arc::clone(&calls);
    let child_step = step("inner");
    let child = Graph::new("child", [child_step.clone().into()], [])
        .unwrap()
        .with_static([StaticRule::when(
            StaticCondition::evaluate(move || {
                observed.fetch_add(1, Ordering::SeqCst);
                true
            }),
            &child_step,
        )]);
    let a = step("a");
    let b = step("b");
    let graph = Graph::new(
        "root",
        [child.clone().into(), a.clone().into(), b.clone().into()],
        [Edge::new(&a, &b)],
    )
    .unwrap()
    .with_static([
        StaticRule::when(false, &child),
        StaticRule::edge(false, &a, &b),
    ]);
    let compiled = graph.compile().unwrap();
    assert_eq!(calls.load(Ordering::SeqCst), 0);
    assert_eq!(compiled.graph().nodes().len(), 3);
    assert!(compiled.projection().nodes()[2].prerequisites().is_empty());
}

#[test]
fn selection_cardinality_and_duplicate_rules_are_explicit_errors() {
    let a = step("a");
    let b = step("b");
    for (left, right, count) in [(false, false, 0), (true, true, 2)] {
        let graph = Graph::new("root", [a.clone().into(), b.clone().into()], [])
            .unwrap()
            .with_static([StaticRule::select([
                Case::new(left, &a),
                Case::new(right, &b),
            ])]);
        assert!(
            matches!(graph.compile(),Err(GraphError::Static(StaticError::SelectionCount(actual))) if actual==count)
        );
    }
    let graph = Graph::new("root", [a.clone().into()], [])
        .unwrap()
        .with_static([StaticRule::when(true, &a), StaticRule::when(false, &a)]);
    assert!(matches!(
        graph.compile(),
        Err(GraphError::Static(StaticError::DuplicateNode(_)))
    ));
}

#[test]
fn static_predicate_panic_is_a_compilation_error() {
    let node = step("guarded");
    let graph = Graph::new("root", [node.clone().into()], [])
        .unwrap()
        .with_static([StaticRule::when(
            StaticCondition::evaluate(|| panic!("static predicate")),
            &node,
        )]);
    assert!(matches!(
        graph.compile(),
        Err(GraphError::Static(StaticError::PredicatePanicked))
    ));
}

#[test]
fn pruned_public_output_is_not_a_successful_value_and_result_edges_cannot_drop_arguments() {
    let a = step("a");
    let b = step("b");
    let child = Graph::with_interface(
        "child",
        [a.clone().into()],
        [],
        GraphInterface::default().output("value", &a).unwrap(),
    )
    .unwrap()
    .with_static([StaticRule::when(false, &a)]);
    child.compile().unwrap();
    let root = Graph::new(
        "root",
        [child.clone().into(), b.clone().into()],
        [Edge::results([child.interface().output("value").unwrap()], &b).unwrap()],
    )
    .unwrap();
    assert!(matches!(
        root.compile(),
        Err(GraphError::UnknownOutputPort(_))
    ));
    let graph = Graph::new(
        "root",
        [a.clone().into(), b.clone().into()],
        [Edge::results([&a], &b).unwrap()],
    )
    .unwrap()
    .with_static([StaticRule::edge(false, &a, &b)]);
    assert!(matches!(
        graph.compile(),
        Err(GraphError::Static(StaticError::ResultEdge(0)))
    ));
}
