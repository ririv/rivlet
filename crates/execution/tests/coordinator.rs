#![cfg(feature = "experimental-mixed-execution")]

use std::collections::HashMap;
use std::num::NonZeroUsize;
use std::sync::Arc;

use action::{Action, ActionContext, ActionError, ActionHandle, Value, ValueType};
use execution::coordinator::{
    ActionBinding, ActionDispatch, ConditionBinding, ConditionDispatch, Coordinator, HostValues,
    InputSite, NativeFailure, Workers,
};
use rivlet::{
    ConditionId, DeclarationNode, DeclarationPrerequisite, DeclaredGraph, DeclaredNodeKind,
    ExecutionSession, ExecutionUpdate, GraphDeclaration, GraphId, NodeActivation, NodeId,
    RuntimeCondition, SessionStatus,
};

fn node(after: &[usize], condition: Option<usize>) -> DeclarationNode {
    DeclarationNode::new(
        GraphId::new(0),
        DeclaredNodeKind::Step,
        NodeActivation::Active {
            prerequisites: after
                .iter()
                .map(|node| DeclarationPrerequisite::Required(NodeId::new(*node)))
                .collect(),
            condition: condition.map(ConditionId::new),
        },
    )
}

fn session(
    nodes: Vec<DeclarationNode>,
    conditions: Vec<RuntimeCondition>,
    skipped: &[usize],
) -> ExecutionSession {
    let compilation = GraphDeclaration::new([DeclaredGraph::Root], nodes, conditions, [])
        .compile()
        .unwrap();
    ExecutionSession::new(
        Arc::new(compilation.into_graph()),
        skipped.iter().copied().map(NodeId::new),
    )
    .unwrap()
}

fn workers() -> Workers {
    Workers::Fixed(NonZeroUsize::new(1).unwrap())
}
fn succeeded(nodes: &[usize]) -> ExecutionUpdate {
    ExecutionUpdate::new(
        nodes.iter().map(|node| (NodeId::new(*node), Ok(()))),
        [],
        [],
    )
}

#[derive(Default)]
struct Host {
    values: HashMap<NodeId, Value>,
    imports: Vec<(NodeId, InputSite)>,
}
impl HostValues for Host {
    fn import(
        &mut self,
        source: NodeId,
        _: ValueType,
        site: InputSite,
    ) -> Result<Value, ActionError> {
        self.imports.push((source, site));
        self.values
            .get(&source)
            .cloned()
            .ok_or_else(|| ActionError::new("host input missing"))
    }
}

struct Double;
impl Action for Double {
    type Inputs = (Option<Arc<i64>>,);
    type Output = i64;
    fn execute(&self, _: &ActionContext, (value,): Self::Inputs) -> Result<i64, ActionError> {
        Ok(value.map_or(-1, |value| *value * 2))
    }
}
fn double(source: usize) -> ActionDispatch {
    ActionDispatch::Native(
        ActionBinding::new(
            ActionHandle::new(Double),
            ActionContext::default(),
            vec![NodeId::new(source)],
        )
        .unwrap(),
    )
}
fn flag(source: usize) -> ConditionDispatch {
    ConditionDispatch::Native(
        ConditionBinding::new(
            vec![ValueType::of::<bool>()],
            vec![NodeId::new(source)],
            |values| {
                Ok(values[0]
                    .as_ref()
                    .is_some_and(|value| *value.downcast::<bool>().unwrap()))
            },
        )
        .unwrap(),
    )
}

#[test]
fn host_native_native_host_flow_needs_no_python_or_js_runtime() {
    let session = session(
        vec![
            node(&[], None),
            node(&[0], None),
            node(&[1], None),
            node(&[2], None),
        ],
        vec![],
        &[],
    );
    let mut run = Coordinator::new(
        session,
        vec![
            ActionDispatch::Host,
            double(0),
            double(1),
            ActionDispatch::Host,
        ],
        vec![],
        workers(),
        true,
    )
    .unwrap();
    let mut host = Host::default();
    host.values.insert(NodeId::new(0), Value::new(21_i64));
    assert_eq!(run.start(1, &mut host).unwrap().actions, [NodeId::new(0)]);
    let first = run.advance(succeeded(&[0]), 1, &mut host).unwrap();
    assert_eq!(first.native_running, 1);
    run.prepare_wait().unwrap().wait();
    let second = run.advance(succeeded(&[]), 0, &mut host).unwrap();
    assert_eq!(second.native_running, 1);
    assert_eq!(*second.results[0].1.downcast::<i64>().unwrap(), 42);
    run.prepare_wait().unwrap().wait();
    let third = run.advance(succeeded(&[]), 0, &mut host).unwrap();
    assert_eq!(*third.results[0].1.downcast::<i64>().unwrap(), 84);
    assert_eq!(third.actions, [NodeId::new(3)]);
    assert_eq!(
        host.imports,
        [(NodeId::new(0), InputSite::Action(NodeId::new(1)))]
    );
    assert_eq!(
        run.advance(succeeded(&[3]), 1, &mut host).unwrap().status,
        SessionStatus::Finished
    );
}

#[test]
fn native_predicate_only_flow_has_no_native_workers_and_fresh_decisions() {
    for enabled in [true, false, true] {
        let session = session(
            vec![node(&[], None), node(&[], Some(0))],
            vec![RuntimeCondition::new([NodeId::new(0)])],
            &[],
        );
        let mut run = Coordinator::new(
            session,
            vec![ActionDispatch::Host; 2],
            vec![flag(0)],
            workers(),
            false,
        )
        .unwrap();
        let mut host = Host::default();
        host.values.insert(NodeId::new(0), Value::new(enabled));
        assert_eq!(run.start(1, &mut host).unwrap().actions, [NodeId::new(0)]);
        let batch = run.advance(succeeded(&[0]), 1, &mut host).unwrap();
        assert_eq!(
            batch.actions,
            if enabled {
                vec![NodeId::new(1)]
            } else {
                vec![]
            }
        );
        assert_eq!(batch.native_running, 0);
        assert!(batch.conditions.is_empty());
        assert!(run.prepare_wait().is_none());
        assert_eq!(
            host.imports,
            [(NodeId::new(0), InputSite::Condition(ConditionId::new(0)))]
        );
    }
}

#[test]
fn resolving_native_condition_preserves_capacity_held_by_host_actions() {
    let session = session(
        vec![node(&[], Some(0)), node(&[], None)],
        vec![RuntimeCondition::new([])],
        &[],
    );
    let condition = ConditionBinding::new(vec![], vec![], |_| Ok(true)).unwrap();
    let mut run = Coordinator::new(
        session,
        vec![ActionDispatch::Host; 2],
        vec![ConditionDispatch::Native(condition)],
        workers(),
        false,
    )
    .unwrap();
    let mut host = Host::default();
    let batch = run.start(1, &mut host).unwrap();
    assert_eq!(batch.actions, [NodeId::new(1)]);
    assert!(batch.conditions.is_empty());
    assert_eq!(
        run.advance(succeeded(&[1]), 1, &mut host).unwrap().actions,
        [NodeId::new(0)]
    );
}

#[test]
fn skipped_native_input_remains_absent_and_is_not_imported_from_host() {
    let session = session(vec![node(&[], None), node(&[0], None)], vec![], &[0]);
    let mut run = Coordinator::new(
        session,
        vec![ActionDispatch::Host, double(0)],
        vec![],
        workers(),
        true,
    )
    .unwrap();
    let mut host = Host::default();
    let batch = run.start(1, &mut host).unwrap();
    assert_eq!(batch.native_running, 1);
    run.prepare_wait().unwrap().wait();
    let batch = run.advance(succeeded(&[]), 0, &mut host).unwrap();
    assert_eq!(batch.status, SessionStatus::Finished);
    assert_eq!(*batch.results[0].1.downcast::<i64>().unwrap(), -1);
    assert!(host.imports.is_empty());
}

#[test]
fn conversion_and_predicate_errors_keep_their_sites_and_allow_independent_work() {
    let session = session(
        vec![
            node(&[], None),
            node(&[0], None),
            node(&[], Some(0)),
            node(&[], None),
        ],
        vec![RuntimeCondition::new([NodeId::new(0)])],
        &[],
    );
    let mut run = Coordinator::new(
        session,
        vec![
            ActionDispatch::Host,
            double(0),
            ActionDispatch::Host,
            ActionDispatch::Host,
        ],
        vec![flag(0)],
        workers(),
        false,
    )
    .unwrap();
    let mut host = Host::default();
    let first = run.start(2, &mut host).unwrap();
    assert_eq!(first.actions, [NodeId::new(0), NodeId::new(3)]);
    // 有意不提供宿主结果；错误属于原生读取方，不把独立任务改成失败。
    let batch = run.advance(succeeded(&[0, 3]), 2, &mut host).unwrap();
    assert_eq!(batch.status, SessionStatus::Failed);
    assert!(
        batch.failures.iter().any(
            |failure| matches!(failure, NativeFailure::Action { node, .. } if node.index() == 1)
        )
    );
    assert!(batch.failures.iter().any(|failure| matches!(failure, NativeFailure::Condition { node, condition, .. } if node.index() == 2 && condition.index() == 0)));
    assert!(batch.actions.is_empty());
}

#[test]
fn predicate_panic_is_a_condition_failure_not_false_or_an_action_failure() {
    let session = session(
        vec![node(&[], Some(0)), node(&[], None)],
        vec![RuntimeCondition::new([])],
        &[],
    );
    let condition = ConditionBinding::new(vec![], vec![], |_| panic!("predicate failed")).unwrap();
    let mut run = Coordinator::new(
        session,
        vec![ActionDispatch::Host; 2],
        vec![ConditionDispatch::Native(condition)],
        workers(),
        false,
    )
    .unwrap();
    let mut host = Host::default();
    let batch = run.start(1, &mut host).unwrap();
    assert_eq!(batch.actions, [NodeId::new(1)]);
    assert!(
        matches!(&batch.failures[..], [NativeFailure::Condition { node, condition, error }]
        if node.index() == 0 && condition.index() == 0 && error.to_string() == "native predicate panicked")
    );
    assert_eq!(
        run.advance(succeeded(&[1]), 1, &mut host).unwrap().status,
        SessionStatus::Failed
    );
}

#[test]
fn completed_composite_unit_is_present_without_host_conversion() {
    let graph = GraphDeclaration::new(
        [DeclaredGraph::Root, DeclaredGraph::OwnedBy(NodeId::new(0))],
        [
            DeclarationNode::new(
                GraphId::new(0),
                DeclaredNodeKind::Composite {
                    child_graph: GraphId::new(1),
                },
                NodeActivation::Active {
                    prerequisites: vec![],
                    condition: None,
                },
            ),
            node(&[], Some(0)),
        ],
        [RuntimeCondition::new([NodeId::new(0)])],
        [],
    )
    .compile()
    .unwrap()
    .into_graph();
    let condition = ConditionBinding::new(
        vec![ValueType::of::<()>()],
        vec![NodeId::new(0)],
        |values| {
            Ok(values[0]
                .as_ref()
                .is_some_and(|value| value.downcast::<()>().is_ok()))
        },
    )
    .unwrap();
    let mut run = Coordinator::new(
        ExecutionSession::new(Arc::new(graph), []).unwrap(),
        vec![ActionDispatch::Host; 2],
        vec![ConditionDispatch::Native(condition)],
        workers(),
        false,
    )
    .unwrap();
    let mut host = Host::default();
    assert_eq!(run.start(1, &mut host).unwrap().actions, [NodeId::new(1)]);
    assert!(host.imports.is_empty());
    assert_eq!(
        run.advance(succeeded(&[1]), 1, &mut host).unwrap().status,
        SessionStatus::Finished
    );
}
