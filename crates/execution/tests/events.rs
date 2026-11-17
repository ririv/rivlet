use action::{Action, ActionContext, ActionError, ActionHandle};
use execution::{
    Execution, ExecutionError, ExecutionOptions, ExecutionStatus, NodeEventDetail, NodeFailure,
};
use graph::{Edge, Graph, Step};
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicUsize, Ordering},
};

struct Work {
    calls: Arc<AtomicUsize>,
    fail: bool,
}
impl Action for Work {
    type Inputs = ();
    type Output = ();
    fn execute(&self, _: &ActionContext, (): ()) -> Result<(), ActionError> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        if self.fail {
            Err(ActionError::new("work failed"))
        } else {
            Ok(())
        }
    }
}
fn work(calls: &Arc<AtomicUsize>, fail: bool) -> Step {
    Step::new(
        "work",
        ActionHandle::new(Work {
            calls: Arc::clone(calls),
            fail,
        }),
    )
    .unwrap()
}

#[test]
fn events_identify_actual_mounts_and_restart_sequence_for_each_execution() {
    let calls = Arc::default();
    let shared = work(&calls, false);
    let left = Graph::new("left", [shared.clone().into()], []).unwrap();
    let right = Graph::new("right", [shared.clone().into()], []).unwrap();
    let root = Graph::new("root", [left.into(), right.into()], []).unwrap();
    for _ in 0..2 {
        let events = Arc::new(Mutex::new(Vec::new()));
        let capture = Arc::clone(&events);
        Execution::new(
            root.compile().unwrap(),
            ExecutionOptions::new(2).unwrap().events(move |event| {
                capture.lock().unwrap().push(event.clone());
                Ok(())
            }),
        )
        .run()
        .unwrap();
        let events = events.lock().unwrap();
        assert!(
            events
                .iter()
                .enumerate()
                .all(|(index, event)| event.sequence == index as u64)
        );
        let mut paths = events
            .iter()
            .filter(|event| {
                event.node_id == shared.id()
                    && matches!(event.state_detail, NodeEventDetail::Started)
            })
            .map(|event| event.node_path.clone())
            .collect::<Vec<_>>();
        paths.sort();
        assert_eq!(paths, [vec![0, 0, 0], vec![0, 1, 0]]);
    }
}
#[test]
fn failures_and_blockage_are_observable_without_losing_independent_results() {
    let calls = Arc::default();
    let fail = work(&calls, true);
    let blocked = work(&calls, false);
    let other = work(&calls, false);
    let root = Graph::new(
        "root",
        [fail.clone().into(), blocked.clone().into(), other.into()],
        [Edge::new(&fail, &blocked)],
    )
    .unwrap();
    let events = Arc::new(Mutex::new(Vec::new()));
    let capture = Arc::clone(&events);
    let outcome = Execution::new(
        root.compile().unwrap(),
        ExecutionOptions::new(2).unwrap().events(move |event| {
            capture.lock().unwrap().push(event.clone());
            Ok(())
        }),
    )
    .run()
    .unwrap();
    assert_eq!(outcome.status(), ExecutionStatus::Failed);
    let events = events.lock().unwrap();
    assert!(events.iter().any(|event| matches!(
        event.state_detail,
        NodeEventDetail::Failed(NodeFailure::Action(_))
    )));
    assert!(
        events
            .iter()
            .any(|event| matches!(event.state_detail, NodeEventDetail::Blocked(_)))
    );
    assert_eq!(calls.load(Ordering::SeqCst), 2);
}
#[test]
fn observer_failure_or_panic_is_not_a_user_action_failure_and_never_cancels_external_source() {
    for panic in [false, true] {
        let source = execution::CancellationSource::new();
        let calls = Arc::default();
        let step = work(&calls, false);
        let result = Execution::new(
            Graph::new("root", [step.into()], [])
                .unwrap()
                .compile()
                .unwrap(),
            ExecutionOptions::new(1)
                .unwrap()
                .cancellation(source.token())
                .events(move |_| {
                    assert!(!panic, "observer panic");
                    Err(ActionError::new("observer failed"))
                }),
        )
        .run();
        assert!(matches!(result, Err(ExecutionError::Observation { .. })));
        assert!(!source.token().is_cancelled());
        assert_eq!(calls.load(Ordering::SeqCst), 0);
    }
}
