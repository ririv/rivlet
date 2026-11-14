//! Optional admission limits shared by related execution sessions.

use std::collections::{HashMap, HashSet, VecDeque};
use std::num::NonZeroUsize;
use std::sync::{Arc, Mutex};

use topology::NodeId;

struct Scope {
    maximum: usize,
    occupied: usize,
}

struct LimitsState {
    scopes: Vec<Scope>,
    revision: u64,
}

/// One execution's shared counters; graph declarations remain unchanged.
#[derive(Clone)]
pub struct ConcurrencyLimits(Arc<Mutex<LimitsState>>);

impl ConcurrencyLimits {
    #[must_use]
    pub fn new(maxima: impl IntoIterator<Item = NonZeroUsize>) -> Self {
        Self(Arc::new(Mutex::new(LimitsState {
            scopes: maxima
                .into_iter()
                .map(|maximum| Scope {
                    maximum: maximum.get(),
                    occupied: 0,
                })
                .collect(),
            revision: 0,
        })))
    }

    #[must_use]
    /// Returns the release revision used to wake related sessions.
    ///
    /// # Panics
    /// Panics if an internal counter operation previously panicked while holding its lock.
    pub fn revision(&self) -> u64 {
        self.0
            .lock()
            .expect("concurrency counters poisoned")
            .revision
    }

    /// Binds each compiled node to its enclosing execution scopes.
    ///
    /// # Errors
    /// Returns an error for unknown or duplicate scope indexes.
    ///
    /// # Panics
    /// Panics if an internal counter operation previously panicked while holding its lock.
    pub fn bind(&self, memberships: Vec<Vec<usize>>) -> Result<SessionConcurrency, &'static str> {
        let count = self
            .0
            .lock()
            .expect("concurrency counters poisoned")
            .scopes
            .len();
        for scopes in &memberships {
            let mut seen = HashSet::new();
            for scope in scopes {
                if *scope >= count || !seen.insert(*scope) {
                    return Err("invalid or duplicate concurrency scope");
                }
            }
        }
        Ok(SessionConcurrency {
            limits: self.clone(),
            memberships,
            occupied: HashSet::new(),
            waiting: HashMap::new(),
        })
    }
}

/// Admission bookkeeping for one session, independent of its action language.
pub struct SessionConcurrency {
    limits: ConcurrencyLimits,
    memberships: Vec<Vec<usize>>,
    occupied: HashSet<NodeId>,
    waiting: HashMap<usize, VecDeque<NodeId>>,
}

impl SessionConcurrency {
    pub(crate) fn node_count(&self) -> usize {
        self.memberships.len()
    }

    pub(crate) fn scopes(&self, node: NodeId) -> &[usize] {
        &self.memberships[node.index()]
    }

    pub(crate) fn acquire(&mut self, node: NodeId) -> bool {
        let mut state = self.limits.0.lock().expect("concurrency counters poisoned");
        let memberships = &self.memberships[node.index()];
        // D 为此动作的受限祖先数。O(D) 原子检查及占用，不逐项持有后等待。
        if let Some(scope) = memberships
            .iter()
            .find(|scope| state.scopes[**scope].occupied == state.scopes[**scope].maximum)
        {
            self.waiting.entry(*scope).or_default().push_back(node);
            return false;
        }
        if !memberships.is_empty() {
            for scope in memberships {
                state.scopes[*scope].occupied += 1;
            }
            self.occupied.insert(node);
        }
        true
    }

    pub(crate) fn release(&mut self, node: NodeId) {
        if !self.occupied.remove(&node) {
            return;
        }
        let mut state = self.limits.0.lock().expect("concurrency counters poisoned");
        for scope in &self.memberships[node.index()] {
            state.scopes[*scope].occupied -= 1;
        }
        state.revision += 1;
    }

    pub(crate) fn retry(&mut self, ready: &mut VecDeque<NodeId>) {
        let state = self.limits.0.lock().expect("concurrency counters poisoned");
        // G 为本 session 等待的受限范围数，W 为本批可重试节点数，O(G + W)。
        // 每个范围只唤醒空余名额数量，避免上限 1 时反复扫描十万个等待动作。
        self.waiting.retain(|scope, nodes| {
            let scope = &state.scopes[*scope];
            let available = scope.maximum - scope.occupied;
            for _ in 0..available.min(nodes.len()) {
                ready.push_back(nodes.pop_front().expect("waiting node exists"));
            }
            !nodes.is_empty()
        });
    }

    pub(crate) fn is_waiting(&self) -> bool {
        !self.waiting.is_empty()
    }
}

impl Drop for SessionConcurrency {
    fn drop(&mut self) {
        // 会话中止或宿主失败时也归还仍持有的额度；状态不会跨执行复用。
        let nodes: Vec<_> = self.occupied.iter().copied().collect();
        for node in nodes {
            self.release(node);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{CompiledExecutionGraph, ExecutionNode, ExecutionSession, ExecutionUpdate};

    fn graph(count: usize) -> Arc<CompiledExecutionGraph> {
        Arc::new(
            CompiledExecutionGraph::new(
                (0..count).map(|_| ExecutionNode::step(None, [], None)),
                [],
            )
            .unwrap(),
        )
    }

    #[test]
    fn waiting_actions_are_readmitted_without_holding_partial_scopes() {
        let limits =
            ConcurrencyLimits::new([NonZeroUsize::new(2).unwrap(), NonZeroUsize::new(1).unwrap()]);
        let binding = limits
            .bind(vec![vec![0, 1], vec![0, 1], vec![0], vec![0]])
            .unwrap();
        let mut session = ExecutionSession::new(graph(4), [])
            .unwrap()
            .with_concurrency(binding)
            .unwrap();
        let batch = session.start(4).unwrap();
        assert_eq!(batch.actions(), &[NodeId::new(0), NodeId::new(2)]);
        let batch = session
            .advance(ExecutionUpdate::new([(NodeId::new(0), Ok(()))], [], []), 4)
            .unwrap();
        assert_eq!(batch.actions().len(), 1);
        let mut completed = vec![(NodeId::new(2), Ok(())), (batch.actions()[0], Ok(()))];
        let batch = session
            .advance(ExecutionUpdate::new(completed.drain(..), [], []), 4)
            .unwrap();
        assert_eq!(batch.actions().len(), 1);
        let final_batch = session
            .advance(
                ExecutionUpdate::new([(batch.actions()[0], Ok(()))], [], []),
                4,
            )
            .unwrap();
        assert_eq!(final_batch.status(), crate::SessionStatus::Finished);
    }

    #[test]
    fn suspended_parent_and_dropped_child_release_shared_admission() {
        let limits = ConcurrencyLimits::new([NonZeroUsize::new(1).unwrap()]);
        let mut parent = ExecutionSession::new(graph(1), [])
            .unwrap()
            .with_concurrency(limits.bind(vec![vec![0]]).unwrap())
            .unwrap();
        assert_eq!(parent.start(1).unwrap().actions(), &[NodeId::new(0)]);
        let mut child = ExecutionSession::new(graph(1), [])
            .unwrap()
            .with_concurrency(limits.bind(vec![vec![0]]).unwrap())
            .unwrap();
        assert!(child.start(1).unwrap().actions().is_empty());
        assert_eq!(parent.suspend_action(NodeId::new(0)).unwrap(), vec![0]);
        assert_eq!(
            child
                .advance(ExecutionUpdate::default(), 1)
                .unwrap()
                .actions(),
            &[NodeId::new(0)]
        );
        drop(child);
        let mut sibling = ExecutionSession::new(graph(1), [])
            .unwrap()
            .with_concurrency(limits.bind(vec![vec![0]]).unwrap())
            .unwrap();
        assert_eq!(sibling.start(1).unwrap().actions(), &[NodeId::new(0)]);
    }
}
