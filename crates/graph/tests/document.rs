use action::{Action, ActionContext, ActionError, ActionHandle};
use graph::{
    Case, Condition, Edge, Graph, GraphInterface, PortName, StaticCondition, StaticRule, Step,
    export_graph, inspection,
};
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering},
};

struct Forbidden;
struct Receiver;
impl Action for Receiver {
    type Inputs = (Option<Arc<()>>,);
    type Output = ();
    fn execute(&self, _: &ActionContext, _: Self::Inputs) -> Result<(), ActionError> {
        panic!("inspection must not execute actions")
    }
}
impl Action for Forbidden {
    type Inputs = ();
    type Output = ();
    fn execute(&self, _: &ActionContext, (): ()) -> Result<(), ActionError> {
        panic!("inspection must not execute actions")
    }
}
fn step(label: &str) -> Step {
    Step::new(label, ActionHandle::new(Forbidden)).unwrap()
}

#[test]
fn raw_export_keeps_static_candidates_and_compiled_export_never_re_evaluates() {
    let calls = Arc::new(AtomicUsize::new(0));
    let observed = Arc::clone(&calls);
    let kept = step("same");
    let removed = step("same");
    let root = Graph::new(
        "workflow",
        [kept.clone().into(), removed.clone().into()],
        [Edge::new(&kept, &removed)],
    )
    .unwrap()
    .with_static([StaticRule::select([
        Case::new(
            StaticCondition::evaluate(move || {
                observed.fetch_add(1, Ordering::SeqCst);
                true
            }),
            &kept,
        ),
        Case::new(false, &removed),
    ])]);
    let document = export_graph(&root).unwrap();
    assert_eq!(document.view().nodes.len(), 3);
    inspection::render_tree(&document, &inspection::RenderOptions::default()).unwrap();
    inspection::render_mermaid(&document, &inspection::RenderOptions::default()).unwrap();
    assert_eq!(calls.load(Ordering::SeqCst), 0);
    let compilation = root.compile().unwrap();
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    let document = export_graph(&compilation).unwrap();
    let inspection::GraphDocument::Compiled(ref compiled) = document else {
        panic!("wrong view")
    };
    assert_eq!(compiled.excluded_nodes, ["0.1"]);
    assert_eq!(compiled.excluded_edges, ["graph:0/edge:0"]);
    inspection::render_tree(&document, &inspection::RenderOptions::default()).unwrap();
    inspection::render_mermaid(&document, &inspection::RenderOptions::default()).unwrap();
    assert_eq!(calls.load(Ordering::SeqCst), 1);
}

#[test]
fn anonymous_and_named_ports_export_result_providers_and_static_edge_decisions() {
    let data = step("data");
    let tail = step("tail");
    let apply = Step::new("apply", ActionHandle::new(Receiver)).unwrap();
    let producer = Graph::with_interface(
        "producer",
        [data.clone().into(), tail.clone().into()],
        [Edge::new(&data, &tail)],
        GraphInterface::default()
            .output(PortName::Anonymous, &data)
            .unwrap()
            .output("report", &tail)
            .unwrap(),
    )
    .unwrap();
    let consumer = Graph::with_interface(
        "consumer",
        [apply.clone().into()],
        [],
        GraphInterface::default().input("apply", [&apply]).unwrap(),
    )
    .unwrap();
    let root = Graph::new(
        "workflow",
        [producer.clone().into(), consumer.clone().into()],
        [Edge::results(
            [producer.interface().output(PortName::Anonymous).unwrap()],
            consumer.interface().input("apply").unwrap(),
        )
        .unwrap()],
    )
    .unwrap();
    for document in [
        export_graph(&root).unwrap(),
        export_graph(&root.compile().unwrap()).unwrap(),
    ] {
        let result = document
            .view()
            .edges
            .iter()
            .find_map(|edge| {
                if let inspection::Edge::Result(edge) = edge {
                    Some(edge)
                } else {
                    None
                }
            })
            .unwrap();
        assert!(
            matches!(&result.source,inspection::ResultSource::Port{owner,name:None,provider} if owner=="0.0"&&provider=="0.0.0")
        );
        assert!(
            matches!(&result.target,inspection::ResultTarget::Port{owner,name:Some(name),consumers} if owner=="0.1"&&name=="apply"&&consumers==&["0.1.0"])
        );
    }
    let guarded = Graph::new(
        "guarded",
        [data.clone().into(), tail.clone().into()],
        [Edge::new(&data, &tail)],
    )
    .unwrap()
    .with_static([StaticRule::edge(false, &data, &tail)]);
    let document = export_graph(&guarded.compile().unwrap()).unwrap();
    assert!(document.view().edges.is_empty());
    assert_eq!(guarded.nodes().len(), 2);
}

#[test]
fn compiled_conditions_keep_each_site_and_readiness_resolves_actual_port_nodes() {
    let ready = step("ready");
    let tail = step("tail");
    let producer = Graph::with_interface(
        "producer",
        [ready.clone().into(), tail.clone().into()],
        [Edge::new(&ready, &tail)],
        GraphInterface::default().output("ready", &ready).unwrap(),
    )
    .unwrap();
    let guard = Condition::new(
        |(_value,): (Option<Arc<()>>,)| -> Result<bool, ActionError> {
            panic!("must not evaluate")
        },
        [producer.interface().output("ready").unwrap().into()],
    )
    .unwrap();
    let first = step("first");
    let second = step("second");
    let consumer = Graph::with_interface(
        "consumer",
        [first.clone().into(), second.clone().into()],
        [],
        GraphInterface::default()
            .input("apply", [&first, &second])
            .unwrap(),
    )
    .unwrap();
    let root = Graph::new(
        "root",
        [producer.clone().into(), consumer.clone().into()],
        [Edge::ports_with_condition(
            producer.interface().output("ready").unwrap(),
            consumer.interface().input("apply").unwrap(),
            guard,
        )],
    )
    .unwrap();
    let compilation = root.compile().unwrap();
    let document = export_graph(&compilation).unwrap();
    let inspection::GraphDocument::Compiled(document) = document else {
        panic!("wrong view")
    };
    assert_eq!(document.conditions.len(), 1);
    assert_eq!(document.conditions[0].inputs, ["0.0.0"]);
    assert!(document.dependencies.iter().any(|dep|matches!(dep,inspection::Dependency::Conditional{source,target,condition} if source=="0.0.0"&&target=="0.1.0"&&condition=="condition:0")));
}
