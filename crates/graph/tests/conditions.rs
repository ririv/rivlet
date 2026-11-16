use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};

use action::{Action, ActionContext, ActionError, ActionHandle};
use graph::{Condition, Edge, Graph, GraphError, GraphStructure, Step};

struct Number;
impl Action for Number {
    type Inputs = ();
    type Output = usize;
    fn execute(&self, _: &ActionContext, (): ()) -> Result<usize, ActionError> {
        Ok(42)
    }
}

fn step(label: &str) -> Step {
    Step::new(label, ActionHandle::new(Number)).unwrap()
}

#[test]
fn declarations_and_compilation_do_not_call_predicates() {
    let calls = Arc::new(AtomicUsize::new(0));
    let captured = Arc::clone(&calls);
    let condition = Condition::new(
        move |()| {
            captured.fetch_add(1, Ordering::SeqCst);
            Ok(true)
        },
        [],
    )
    .unwrap();
    let a = step("a");
    let b = Step::with_condition("b", ActionHandle::new(Number), condition.clone()).unwrap();
    let body = Arc::new(
        GraphStructure::new(
            [a.clone().into(), b.clone().into()],
            [Edge::with_condition(&a, &b, condition.clone())],
        )
        .unwrap(),
    );
    let root = Graph::with_condition("root", body, condition).unwrap();
    let compilation = root.compile().unwrap();
    assert_eq!(compilation.graph().conditions().len(), 3);
    assert_eq!(calls.load(Ordering::SeqCst), 0);
}

#[test]
fn predicate_inputs_are_checked_before_execution() {
    let a = step("a");
    assert!(matches!(
        Condition::new(|(_value,): (Option<Arc<usize>>,)| Ok(true), []),
        Err(GraphError::ConditionInputCount {
            expected: 1,
            actual: 0
        })
    ));
    let condition =
        Condition::new(|(_value,): (Option<Arc<String>>,)| Ok(true), [(&a).into()]).unwrap();
    let b = Step::with_condition("b", ActionHandle::new(Number), condition).unwrap();
    assert!(matches!(
        GraphStructure::new([a.into(), b.into()], [])
            .unwrap()
            .compile_structure(),
        Err(GraphError::ResultTypeMismatch { .. })
    ));
}

#[test]
fn predicate_sources_must_belong_to_the_actual_local_scope() {
    let outside = step("outside");
    let condition = Condition::new(
        |(_value,): (Option<Arc<usize>>,)| Ok(true),
        [(&outside).into()],
    )
    .unwrap();
    let target = Step::with_condition("target", ActionHandle::new(Number), condition).unwrap();
    assert!(
        matches!(GraphStructure::new([target.into()], []), Err(GraphError::UnknownNode(id)) if id == outside.id())
    );
}

#[test]
fn condition_reads_participate_in_cycle_validation() {
    let source = step("source");
    let condition = Condition::new(
        |(_value,): (Option<Arc<usize>>,)| Ok(true),
        [(&source).into()],
    )
    .unwrap();
    let target = Step::with_condition("target", ActionHandle::new(Number), condition).unwrap();
    assert!(matches!(
        GraphStructure::new(
            [source.clone().into(), target.clone().into()],
            [Edge::new(&target, &source)]
        ),
        Err(GraphError::Topology(_))
    ));
}
