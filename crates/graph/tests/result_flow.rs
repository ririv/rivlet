use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use action::{
    NativeAction, NativeActionContext, NativeActionError, NativeActionExecutor, NativeActionHandle,
    NativeValue,
};
use graph::{
    Compilation, Edge, Graph, GraphError, GraphInterface, GraphStructure, Leaf, ProjectedKind,
};
use rivlet::{ExecutionEvent, ExecutionSession, ExecutionUpdate, NodeId, NodeState, SessionStatus};

// 测试宿主只管理值与 worker，依赖状态完全由正式 ExecutionSession 推进。
fn run(
    compilation: &Compilation,
    skipped: Vec<NodeId>,
) -> (SessionStatus, HashMap<NodeId, NativeValue>) {
    let mut session =
        ExecutionSession::new(Arc::new(compilation.graph().clone()), skipped).unwrap();
    let executor = NativeActionExecutor::new(2).unwrap();
    let mut values = HashMap::new();
    let mut batch = session.start(2).unwrap();
    loop {
        for event in batch.events() {
            if let ExecutionEvent::CompositeCompleted(node) = event {
                values.insert(*node, NativeValue::new(()));
            }
        }
        for node in batch.actions() {
            let projected = compilation.node(*node);
            let ProjectedKind::Leaf(action) = projected.kind() else {
                panic!("composite was dispatched");
            };
            let inputs = projected
                .inputs()
                .iter()
                .map(|source| match session.state(*source) {
                    Some(NodeState::Skipped) => None,
                    Some(NodeState::Finished) => Some(values[source].clone()),
                    state => panic!("source is not ready: {state:?}"),
                })
                .collect();
            executor
                .submit(
                    node.index(),
                    action.clone(),
                    NativeActionContext::default(),
                    inputs,
                )
                .unwrap();
        }
        if batch.status() != SessionStatus::Running {
            return (batch.status(), values);
        }
        executor.wait_for_completion();
        let completed = executor
            .drain_completions()
            .into_iter()
            .map(|completion| {
                let node = NodeId::new(completion.node());
                let result = completion
                    .into_result()
                    .map(|value| {
                        values.insert(node, value);
                    })
                    .map_err(|_| ());
                (node, result)
            })
            .collect::<Vec<_>>();
        let capacity = completed.len();
        batch = session
            .advance(ExecutionUpdate::new(completed, [], []), capacity)
            .unwrap();
    }
}

struct Plan {
    bytes: Vec<u8>,
}
struct Produce;
impl NativeAction for Produce {
    type Inputs = ();
    type Output = Plan;
    fn execute(&self, _: &NativeActionContext, (): ()) -> Result<Plan, NativeActionError> {
        Ok(Plan {
            bytes: vec![7; 4096],
        })
    }
}

struct Consume(Arc<Mutex<Vec<usize>>>);
impl NativeAction for Consume {
    type Inputs = (Option<Arc<Plan>>,);
    type Output = usize;
    fn execute(
        &self,
        _: &NativeActionContext,
        (plan,): Self::Inputs,
    ) -> Result<usize, NativeActionError> {
        match plan {
            Some(plan) => {
                self.0.lock().unwrap().push(Arc::as_ptr(&plan) as usize);
                Ok(plan.bytes.len())
            }
            None => Ok(0),
        }
    }
}

#[test]
fn arbitrary_nonclone_payload_crosses_forwarded_shared_graphs_without_copying() {
    let pointers = Arc::new(Mutex::new(Vec::new()));
    let producer = Leaf::new("produce", NativeActionHandle::new(Produce)).unwrap();
    let body = Arc::new(
        GraphStructure::with_interface(
            [producer.clone().into()],
            [],
            GraphInterface::default().output("plan", &producer).unwrap(),
        )
        .unwrap(),
    );
    let inner = Graph::new("inner", body).unwrap();
    let forwarded = Graph::new(
        "producer",
        Arc::new(
            GraphStructure::with_interface(
                [inner.clone().into()],
                [],
                GraphInterface::default()
                    .output("value", inner.interface().output("plan").unwrap())
                    .unwrap(),
            )
            .unwrap(),
        ),
    )
    .unwrap();
    let consume = Leaf::new(
        "consume",
        NativeActionHandle::new(Consume(Arc::clone(&pointers))),
    )
    .unwrap();
    let shared = Arc::new(
        GraphStructure::with_interface(
            [consume.clone().into()],
            [],
            GraphInterface::default()
                .input("apply", [&consume])
                .unwrap(),
        )
        .unwrap(),
    );
    let left = Graph::new("left", Arc::clone(&shared)).unwrap();
    let right = Graph::new("right", shared).unwrap();
    let root = GraphStructure::new(
        [
            forwarded.clone().into(),
            left.clone().into(),
            right.clone().into(),
        ],
        [
            Edge::results(
                [forwarded.interface().output("value").unwrap()],
                left.interface().input("apply").unwrap(),
            )
            .unwrap(),
            Edge::results(
                [forwarded.interface().output("value").unwrap()],
                right.interface().input("apply").unwrap(),
            )
            .unwrap(),
        ],
    )
    .unwrap();
    let compilation = root.compile_structure().unwrap();
    let (status, results) = run(&compilation, Vec::new());
    assert_eq!(status, SessionStatus::Finished);
    let pointers = pointers.lock().unwrap();
    assert_eq!(pointers.len(), 2);
    assert_eq!(pointers[0], pointers[1]);
    assert_eq!(
        results
            .values()
            .filter_map(|value| value.downcast::<usize>().ok())
            .map(|value| *value)
            .collect::<Vec<_>>(),
        [4096, 4096]
    );
}

#[test]
fn skipped_result_is_none_not_an_invented_unit_value() {
    let producer = Leaf::new("produce", NativeActionHandle::new(Produce)).unwrap();
    let consume = Leaf::new("consume", NativeActionHandle::new(Consume(Arc::default()))).unwrap();
    let compilation = GraphStructure::new(
        [producer.clone().into(), consume.clone().into()],
        [Edge::results([&producer], &consume).unwrap()],
    )
    .unwrap()
    .compile_structure()
    .unwrap();
    let source = compilation
        .graph()
        .nodes()
        .iter()
        .enumerate()
        .find_map(|(index, _)| {
            let node = NodeId::new(index);
            (compilation.node(node).identity() == producer.id()).then_some(node)
        })
        .unwrap();
    let (status, values) = run(&compilation, vec![source]);
    assert_eq!(status, SessionStatus::Finished);
    assert!(!values.contains_key(&source));
    assert!(
        values
            .values()
            .any(|value| value.downcast::<usize>().is_ok_and(|value| *value == 0))
    );
}

struct Text;
impl NativeAction for Text {
    type Inputs = ();
    type Output = String;
    fn execute(&self, _: &NativeActionContext, (): ()) -> Result<String, NativeActionError> {
        Ok(String::from("wrong"))
    }
}

#[test]
fn compiler_checks_concrete_types_and_complete_input_groups() {
    let wrong = Leaf::new("text", NativeActionHandle::new(Text)).unwrap();
    let consume = Leaf::new("consume", NativeActionHandle::new(Consume(Arc::default()))).unwrap();
    let graph = GraphStructure::new(
        [wrong.clone().into(), consume.clone().into()],
        [Edge::results([&wrong], &consume).unwrap()],
    )
    .unwrap();
    assert!(matches!(
        graph.compile_structure(),
        Err(GraphError::ResultTypeMismatch { .. })
    ));
    let unbound = GraphStructure::new([consume.into()], []).unwrap();
    assert!(matches!(
        unbound.compile_structure(),
        Err(GraphError::ResultInputCount { .. })
    ));
}

#[test]
fn result_cycle_is_checked_after_resolving_actual_endpoints() {
    // 这里用同型的 usize 传递来构成真正的结果依赖环。
    struct Size;
    impl NativeAction for Size {
        type Inputs = (Option<Arc<usize>>,);
        type Output = usize;
        fn execute(
            &self,
            _: &NativeActionContext,
            (value,): Self::Inputs,
        ) -> Result<usize, NativeActionError> {
            Ok(*value.unwrap())
        }
    }
    let a = Leaf::new("a", NativeActionHandle::new(Size)).unwrap();
    let b = Leaf::new("b", NativeActionHandle::new(Size)).unwrap();
    let graph = GraphStructure::new(
        [a.clone().into(), b.clone().into()],
        [
            Edge::results([&a], &b).unwrap(),
            Edge::results([&b], &a).unwrap(),
        ],
    )
    .unwrap();
    assert!(matches!(
        graph.compile_structure(),
        Err(GraphError::Declaration(_))
    ));
}

struct Number;
impl NativeAction for Number {
    type Inputs = ();
    type Output = i64;
    fn execute(&self, _: &NativeActionContext, (): ()) -> Result<i64, NativeActionError> {
        Ok(21)
    }
}

struct Pair;
impl NativeAction for Pair {
    type Inputs = (Option<Arc<String>>, Option<Arc<i64>>);
    type Output = String;
    fn execute(
        &self,
        _: &NativeActionContext,
        (text, number): Self::Inputs,
    ) -> Result<String, NativeActionError> {
        Ok(format!("{}:{}", text.unwrap(), number.unwrap()))
    }
}

#[test]
fn ordered_heterogeneous_tuple_inputs_ignore_declaration_and_completion_order() {
    let number = Leaf::new("number", NativeActionHandle::new(Number)).unwrap();
    let text = Leaf::new("text", NativeActionHandle::new(Text)).unwrap();
    let pair = Leaf::new("pair", NativeActionHandle::new(Pair)).unwrap();
    let compilation = GraphStructure::new(
        [
            number.clone().into(),
            text.clone().into(),
            pair.clone().into(),
        ],
        [Edge::results([&text, &number], &pair).unwrap()],
    )
    .unwrap()
    .compile_structure()
    .unwrap();
    let (status, values) = run(&compilation, Vec::new());
    assert_eq!(status, SessionStatus::Finished);
    let result = compilation
        .graph()
        .nodes()
        .iter()
        .enumerate()
        .find_map(|(index, _)| {
            let node = NodeId::new(index);
            (compilation.node(node).identity() == pair.id()).then_some(node)
        })
        .unwrap();
    assert_eq!(
        values[&result].downcast::<String>().unwrap().as_str(),
        "wrong:21"
    );
    let wrong = GraphStructure::new(
        [
            number.clone().into(),
            text.clone().into(),
            pair.clone().into(),
        ],
        [Edge::results([&number, &text], &pair).unwrap()],
    )
    .unwrap();
    assert!(matches!(
        wrong.compile_structure(),
        Err(GraphError::ResultTypeMismatch { .. })
    ));
}
