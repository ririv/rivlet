use action::{Action, ActionContext, ActionError, ActionHandle};
use execution::{ConcurrencyError, Execution, ExecutionError, ExecutionOptions, ExecutionStatus};
use graph::{Graph, Step};
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicUsize, Ordering},
};
use std::time::Duration;

#[derive(Default)]
struct Tracker {
    active: AtomicUsize,
    peak: AtomicUsize,
    threads: Mutex<std::collections::HashSet<std::thread::ThreadId>>,
}
struct Busy(Arc<Tracker>, Arc<Tracker>);
impl Action for Busy {
    type Inputs = ();
    type Output = ();
    fn execute(&self, _: &ActionContext, (): ()) -> Result<(), ActionError> {
        for tracker in [&self.0, &self.1] {
            let active = tracker.active.fetch_add(1, Ordering::SeqCst) + 1;
            tracker.peak.fetch_max(active, Ordering::SeqCst);
            tracker
                .threads
                .lock()
                .unwrap()
                .insert(std::thread::current().id());
        }
        std::thread::sleep(Duration::from_millis(2));
        for tracker in [&self.0, &self.1] {
            tracker.active.fetch_sub(1, Ordering::SeqCst);
        }
        Ok(())
    }
}
fn group(label: &str, local: &Arc<Tracker>, global: &Arc<Tracker>) -> Graph {
    Graph::new(
        label,
        (0..8).map(|_| {
            Step::new(
                "work",
                ActionHandle::new(Busy(Arc::clone(local), Arc::clone(global))),
            )
            .unwrap()
            .into()
        }),
        [],
    )
    .unwrap()
}
#[test]
fn nested_limits_share_one_pool_and_intersect_all_ancestors() {
    let global = Arc::default();
    let a = Arc::default();
    let b = Arc::default();
    let left = group("left", &a, &global);
    let right = group("right", &b, &global);
    let root = Graph::new("root", [left.clone().into(), right.clone().into()], []).unwrap();
    let options = ExecutionOptions::new(8)
        .unwrap()
        .max_concurrency(&root, 3)
        .unwrap()
        .max_concurrency(&left, 1)
        .unwrap()
        .max_concurrency(&right, 2)
        .unwrap();
    let outcome = Execution::new(root.compile().unwrap(), options)
        .run()
        .unwrap();
    assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
    assert_eq!(a.peak.load(Ordering::SeqCst), 1);
    assert_eq!(b.peak.load(Ordering::SeqCst), 2);
    assert!(global.peak.load(Ordering::SeqCst) <= 3);
    assert!(global.threads.lock().unwrap().len() <= 8);
}
struct Generate(Graph);
impl Action for Generate {
    type Inputs = ();
    type Output = Graph;
    fn execute(&self, _: &ActionContext, (): ()) -> Result<Graph, ActionError> {
        Ok(self.0.clone())
    }
}
#[test]
fn generated_sessions_inherit_limits_and_release_the_parent_slot() {
    for maximum in [1, 2] {
        let global = Arc::default();
        let local = Arc::default();
        let generators = (0..3)
            .map(|_| {
                Step::new(
                    "generate",
                    ActionHandle::new(Generate(group("child", &local, &global))),
                )
                .unwrap()
                .into()
            })
            .collect::<Vec<_>>();
        let root = Graph::new("root", generators, []).unwrap();
        let options = ExecutionOptions::new(8)
            .unwrap()
            .max_concurrency(&root, maximum)
            .unwrap();
        let outcome = Execution::new(root.compile().unwrap(), options)
            .run()
            .unwrap();
        assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
        assert_eq!(global.peak.load(Ordering::SeqCst), maximum);
    }
}
#[test]
fn invalid_or_ambiguous_limits_fail_before_actions_start() {
    let global = Arc::default();
    let local = Arc::default();
    let shared = group("shared", &local, &global);
    assert!(matches!(
        ExecutionOptions::new(1)
            .unwrap()
            .max_concurrency(&shared, 0),
        Err(ExecutionError::Concurrency(
            ConcurrencyError::InvalidMaximum
        ))
    ));
    assert!(matches!(
        ExecutionOptions::new(1)
            .unwrap()
            .max_concurrency(&shared, 1)
            .unwrap()
            .max_concurrency(&shared, 2),
        Err(ExecutionError::Concurrency(
            ConcurrencyError::DuplicateNode(_)
        ))
    ));
    let empty = Graph::new("empty", [], []).unwrap();
    let options = ExecutionOptions::new(1)
        .unwrap()
        .max_concurrency(&shared, 1)
        .unwrap();
    assert!(matches!(
        Execution::new(empty.compile().unwrap(), options).run(),
        Err(ExecutionError::Concurrency(ConcurrencyError::UnknownNode(
            _
        )))
    ));
    let left = Graph::new("left", [shared.clone().into()], []).unwrap();
    let right = Graph::new("right", [shared.clone().into()], []).unwrap();
    let root = Graph::new("root", [left.into(), right.into()], []).unwrap();
    let options = ExecutionOptions::new(1)
        .unwrap()
        .max_concurrency(&shared, 1)
        .unwrap();
    assert!(matches!(
        Execution::new(root.compile().unwrap(), options).run(),
        Err(ExecutionError::Concurrency(
            ConcurrencyError::AmbiguousNode(_)
        ))
    ));
    assert_eq!(global.peak.load(Ordering::SeqCst), 0);
}
