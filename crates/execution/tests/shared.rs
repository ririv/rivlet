use action::{Action, ActionContext, ActionError, ActionHandle, AsyncAction};
use execution::{Execution, ExecutionOptions, ExecutionStatus, NodeFailure, NodeOutcome};
use graph::{Edge, Graph, Step};
use rivlet::NodeId;
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering},
};

#[derive(Default)]
struct Progress(AtomicUsize);
struct Record;
impl Action for Record {
    type Inputs = ();
    type Output = ();
    fn execute(&self, ctx: &ActionContext, (): ()) -> Result<(), ActionError> {
        ctx.shared::<Progress>()?.0.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
}
impl AsyncAction for Record {
    type Inputs = ();
    type Output = ();
    async fn execute(&self, ctx: ActionContext, (): ()) -> Result<(), ActionError> {
        tokio::task::yield_now().await;
        ctx.shared::<Progress>()?.0.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
}
struct Generate(Graph);
impl Action for Generate {
    type Inputs = ();
    type Output = Graph;
    fn execute(&self, ctx: &ActionContext, (): ()) -> Result<Graph, ActionError> {
        ctx.shared::<Progress>()?.0.fetch_add(1, Ordering::SeqCst);
        Ok(self.0.clone())
    }
}
fn record(asynchronous: bool) -> Step {
    let action = if asynchronous {
        ActionHandle::new_async(Record)
    } else {
        ActionHandle::new(Record)
    };
    Step::new("record", action).unwrap()
}
fn workflow(asynchronous: bool) -> Arc<graph::Compilation> {
    let child = Graph::new("dynamic", [record(asynchronous).into()], []).unwrap();
    let factory = Step::new("generate", ActionHandle::new(Generate(child))).unwrap();
    let declared = Graph::new("declared", [record(false).into()], []).unwrap();
    let mut nodes = (0..12)
        .map(|index| record(asynchronous && index % 2 == 0).into())
        .collect::<Vec<_>>();
    nodes.extend([declared.into(), factory.into()]);
    Arc::new(Graph::new("root", nodes, []).unwrap().compile().unwrap())
}

#[test]
fn workers_declared_and_generated_children_inherit_one_object_per_execution() {
    let compilation = workflow(false);
    for _ in 0..2 {
        let progress = Arc::new(Progress::default());
        let options = ExecutionOptions::new(4)
            .unwrap()
            .shared(Arc::clone(&progress));
        assert!(!format!("{options:?}").contains("AtomicUsize"));
        let outcome = Execution::new(Arc::clone(&compilation), options)
            .run()
            .unwrap();
        assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
        assert_eq!(progress.0.load(Ordering::SeqCst), 15);
        assert_eq!(Arc::strong_count(&progress), 1);
    }
}

#[tokio::test(flavor = "current_thread")]
async fn asynchronous_and_blocking_actions_use_the_same_object_in_dynamic_children() {
    let progress = Arc::new(Progress::default());
    let options = ExecutionOptions::new(3)
        .unwrap()
        .shared(Arc::clone(&progress));
    let outcome = Execution::new(workflow(true), options)
        .run_async()
        .await
        .unwrap();
    assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
    assert_eq!(progress.0.load(Ordering::SeqCst), 15);
    assert_eq!(Arc::strong_count(&progress), 1);
}

struct Number;
impl Action for Number {
    type Inputs = ();
    type Output = usize;
    fn execute(&self, _: &ActionContext, (): ()) -> Result<usize, ActionError> {
        Ok(7)
    }
}
struct Use;
impl Action for Use {
    type Inputs = (Option<Arc<usize>>,);
    type Output = usize;
    fn execute(&self, ctx: &ActionContext, (input,): Self::Inputs) -> Result<usize, ActionError> {
        let value = *input.ok_or_else(|| ActionError::new("missing input"))?;
        ctx.shared::<Progress>()?
            .0
            .fetch_add(value, Ordering::SeqCst);
        Ok(value * 2)
    }
}

#[test]
fn shared_state_never_replaces_result_edges_or_their_output_values() {
    let number = Step::new("number", ActionHandle::new(Number)).unwrap();
    let use_value = Step::new("use", ActionHandle::new(Use)).unwrap();
    let graph = Graph::new(
        "root",
        [number.clone().into(), use_value.clone().into()],
        [Edge::results([&number], &use_value).unwrap()],
    )
    .unwrap();
    let progress = Arc::new(Progress::default());
    let outcome = Execution::new(
        graph.compile().unwrap(),
        ExecutionOptions::new(2)
            .unwrap()
            .shared(Arc::clone(&progress)),
    )
    .run()
    .unwrap();
    assert_eq!(progress.0.load(Ordering::SeqCst), 7);
    let NodeOutcome::Succeeded(value) = outcome.node(NodeId::new(2)).unwrap() else {
        panic!("result is not successful")
    };
    assert_eq!(*value.downcast::<usize>().unwrap(), 14);
}

#[test]
fn missing_and_wrong_types_fail_only_actions_that_request_the_object() {
    let access = record(false);
    let independent = Step::new("independent", ActionHandle::new(Number)).unwrap();
    let compilation = Arc::new(
        Graph::new("root", [access.into(), independent.into()], [])
            .unwrap()
            .compile()
            .unwrap(),
    );
    for options in [
        ExecutionOptions::new(2).unwrap(),
        ExecutionOptions::new(2)
            .unwrap()
            .shared(Arc::new(String::from("wrong type"))),
    ] {
        let outcome = Execution::new(Arc::clone(&compilation), options)
            .run()
            .unwrap();
        assert_eq!(outcome.status(), ExecutionStatus::Failed);
        assert!(matches!(
            outcome.node(NodeId::new(1)).unwrap(),
            NodeOutcome::Failed(NodeFailure::Action(_))
        ));
        assert!(matches!(
            outcome.node(NodeId::new(2)).unwrap(),
            NodeOutcome::Succeeded(_)
        ));
    }
}
