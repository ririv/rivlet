use rivlet::{
    CompiledExecutionGraph, ConditionId, ConditionResolution, ExecutionNode, ExecutionSession,
    ExecutionUpdate, NodeId, NodeState, Prerequisite, RuntimeCondition, SessionStatus,
};
use std::sync::Arc;

fn node(n: usize) -> NodeId {
    NodeId::new(n)
}

#[test]
fn cancellation_preserves_running_work_and_never_dispatches_a_dependent() {
    let graph = Arc::new(
        CompiledExecutionGraph::new(
            [
                ExecutionNode::composite(None, [], None),
                ExecutionNode::step(Some(node(0)), [], None),
                ExecutionNode::step(Some(node(0)), [Prerequisite::Required(node(1))], None),
            ],
            [],
        )
        .unwrap(),
    );
    let mut session = ExecutionSession::new(graph, []).unwrap();
    assert_eq!(session.start(1).unwrap().actions(), [node(1)]);
    session.request_cancel();
    let waiting = session.advance(ExecutionUpdate::default(), 1).unwrap();
    assert_eq!(waiting.status(), SessionStatus::Running);
    assert!(waiting.actions().is_empty());
    assert_eq!(session.state(node(2)), Some(NodeState::Cancelled));
    let last = session
        .advance(ExecutionUpdate::new([(node(1), Ok(()))], [], []), 1)
        .unwrap();
    assert_eq!(last.status(), SessionStatus::Cancelled);
    assert_eq!(session.state(node(1)), Some(NodeState::Finished));
    assert_eq!(session.state(node(0)), Some(NodeState::Cancelled));
}

#[test]
fn failure_during_cancellation_wins_and_late_cancellation_is_a_noop() {
    for failed in [false, true] {
        let graph = Arc::new(
            CompiledExecutionGraph::new([ExecutionNode::step(None, [], None)], []).unwrap(),
        );
        let mut session = ExecutionSession::new(graph, []).unwrap();
        session.start(1).unwrap();
        if failed {
            session.request_cancel();
        }
        let last = session
            .advance(
                ExecutionUpdate::new([(node(0), if failed { Err(()) } else { Ok(()) })], [], []),
                1,
            )
            .unwrap();
        let expected = if failed {
            SessionStatus::Failed
        } else {
            SessionStatus::Finished
        };
        assert_eq!(last.status(), expected);
        session.request_cancel();
        assert_eq!(
            session
                .advance(ExecutionUpdate::default(), 1)
                .unwrap()
                .status(),
            expected
        );
    }
}

#[test]
fn pre_cancel_and_outstanding_condition_have_distinct_terminal_acknowledgements() {
    let graph = Arc::new(
        CompiledExecutionGraph::new(
            [ExecutionNode::step(None, [], Some(ConditionId::new(0)))],
            [RuntimeCondition::new([])],
        )
        .unwrap(),
    );
    let mut session = ExecutionSession::new(Arc::clone(&graph), []).unwrap();
    session.request_cancel();
    let first = session.start(1).unwrap();
    assert_eq!(first.status(), SessionStatus::Cancelled);
    assert!(first.conditions().is_empty());
    let mut session = ExecutionSession::new(graph, []).unwrap();
    assert_eq!(session.start(1).unwrap().conditions().len(), 1);
    session.request_cancel();
    assert_eq!(
        session
            .advance(ExecutionUpdate::default(), 1)
            .unwrap()
            .status(),
        SessionStatus::Running
    );
    let last = session
        .advance(
            ExecutionUpdate::new(
                [],
                [ConditionResolution::cancelled(ConditionId::new(0))],
                [],
            ),
            1,
        )
        .unwrap();
    assert_eq!(last.status(), SessionStatus::Cancelled);
}

#[test]
fn cancellation_releases_admission_for_a_separate_session() {
    use rivlet::ConcurrencyLimits;
    use std::num::NonZeroUsize;
    let graph = Arc::new(
        CompiledExecutionGraph::new(
            [
                ExecutionNode::step(None, [], None),
                ExecutionNode::step(None, [], None),
            ],
            [],
        )
        .unwrap(),
    );
    let limits = ConcurrencyLimits::new([NonZeroUsize::new(1).unwrap()]);
    let mut first = ExecutionSession::new(Arc::clone(&graph), [])
        .unwrap()
        .with_concurrency(limits.bind(vec![vec![0], vec![0]]).unwrap())
        .unwrap();
    let batch = first.start(2).unwrap();
    assert_eq!(batch.actions().len(), 1);
    let last = first
        .advance(
            ExecutionUpdate::default().cancelled(batch.actions().iter().copied()),
            2,
        )
        .unwrap();
    assert_eq!(last.status(), SessionStatus::Cancelled);
    let mut second = ExecutionSession::new(graph, [])
        .unwrap()
        .with_concurrency(limits.bind(vec![vec![0], vec![0]]).unwrap())
        .unwrap();
    assert_eq!(second.start(2).unwrap().actions().len(), 1);
}
