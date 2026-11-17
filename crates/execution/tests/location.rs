use action::{Action, ActionContext, ActionError, ActionHandle, AsyncAction};
use execution::{Execution, ExecutionOptions, NodeEventDetail};
use graph::{Graph, StaticRule, Step};
use rivlet::NodeIdentity;
use std::sync::{Arc, Mutex};

#[derive(Debug, Eq, PartialEq)]
struct Location {
    id: NodeIdentity,
    path: Vec<usize>,
    labels: Vec<String>,
}
struct Read;
impl Action for Read {
    type Inputs = ();
    type Output = Location;
    fn execute(&self, ctx: &ActionContext, (): ()) -> Result<Location, ActionError> {
        Ok(Location {
            id: ctx.node_id()?,
            path: ctx.node_path()?.to_vec(),
            labels: ctx.label_path()?.to_vec(),
        })
    }
}
impl AsyncAction for Read {
    type Inputs = ();
    type Output = Location;
    async fn execute(&self, ctx: ActionContext, (): ()) -> Result<Location, ActionError> {
        tokio::task::yield_now().await;
        Action::execute(self, &ctx, ())
    }
}
struct Generate(Graph);
impl Action for Generate {
    type Inputs = ();
    type Output = Graph;
    fn execute(&self, _: &ActionContext, (): ()) -> Result<Graph, ActionError> {
        Ok(self.0.clone())
    }
}
fn read() -> Step {
    Step::new("read", ActionHandle::new(Read)).unwrap()
}

#[test]
fn context_and_events_identify_the_same_repeated_actual_mounts_after_pruning() {
    let shared = read();
    let ignored = read();
    let left = Graph::new("left", [ignored.clone().into(), shared.clone().into()], [])
        .unwrap()
        .with_static([StaticRule::when(false, &ignored)]);
    let right = Graph::new("right", [shared.clone().into()], []).unwrap();
    let root = Graph::new("root", [left.into(), right.into()], []).unwrap();
    let events = Arc::new(Mutex::new(Vec::new()));
    let captured = Arc::clone(&events);
    let shared_id = shared.id();
    let outcome = Execution::new(
        root.compile().unwrap(),
        ExecutionOptions::new(2).unwrap().events(move |event| {
            if event.node_id == shared_id && matches!(event.state_detail, NodeEventDetail::Started)
            {
                captured.lock().unwrap().push(event.node_path.clone());
            }
            Ok(())
        }),
    )
    .run()
    .unwrap();
    let mut paths = Vec::new();
    for (_, result) in outcome.nodes() {
        if let execution::NodeOutcome::Succeeded(value) = result {
            if value.type_id() == action::ValueType::of::<Location>().id() {
                let location = value.downcast::<Location>().unwrap();
                assert_eq!(location.id, shared.id());
                assert_eq!(
                    location.labels,
                    if location.path[1] == 0 {
                        ["root", "left", "read"]
                    } else {
                        ["root", "right", "read"]
                    }
                );
                paths.push(location.path.clone());
            }
        }
    }
    paths.sort();
    let mut observed = events.lock().unwrap().clone();
    observed.sort();
    assert_eq!(paths, [vec![0, 0, 1], vec![0, 1, 0]]);
    assert_eq!(observed, paths);
}

#[tokio::test(flavor = "current_thread")]
async fn generated_graphs_and_async_actions_keep_all_runtime_ancestor_paths() {
    let read = Step::new("read", ActionHandle::new_async(Read)).unwrap();
    let child = Graph::new("generated", [read.clone().into()], []).unwrap();
    let generate = Step::new("generate", ActionHandle::new(Generate(child))).unwrap();
    let outcome = Execution::new(
        generate.compile().unwrap(),
        ExecutionOptions::new(1).unwrap(),
    )
    .run_async()
    .await
    .unwrap();
    let node = outcome.compilation().node_index(&generate).unwrap();
    let location = outcome
        .subgraph(node)
        .unwrap()
        .result::<Location>(&read)
        .unwrap()
        .unwrap();
    assert_eq!(location.id, read.id());
    assert_eq!(location.path, [0, 0, 0]);
    assert_eq!(location.labels, ["generate", "generated", "read"]);
}
