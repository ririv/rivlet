//! One session tree sharing a worker pool, cancellation and event sequence.

use super::{results::Results, workers::PoolOwner};
use crate::{
    ConditionOutcome, ExecutionError, ExecutionOptions, ExecutionOutcome, ExecutionStatus,
    NodeEventDetail, NodeFailure, NodeOutcome, events::Observations,
};
use action::{
    ActionContext, ActionError, ActionFuture, CancellationRegistration, CancellationToken, Value,
    ValueType,
};
use graph::{Compilation, ConditionEvaluation, Graph, ProjectedKind};
use rivlet::{
    ConditionId, ConditionResolution, ExecutionEvent as SchedulerEvent, ExecutionSession,
    ExecutionUpdate, FailureCause, NodeId, SessionStatus,
};
use std::collections::{BTreeSet, HashMap, VecDeque};
use std::sync::Arc;

pub(super) type ConditionWork = (ConditionId, ConditionEvaluation);
#[derive(Clone, Copy)]
pub(super) struct ActionSite {
    frame: usize,
    node: NodeId,
}
#[derive(Clone, Copy, PartialEq, Eq)]
pub(super) enum Mode {
    Sync,
    Async,
}
pub(super) struct Resolution {
    frame: usize,
    value: ConditionResolution,
}

#[derive(Default)]
struct Updates {
    completed: Vec<(NodeId, Result<(), ()>)>,
    conditions: Vec<ConditionResolution>,
    cancelled: Vec<NodeId>,
}
struct Parent {
    frame: usize,
    node: NodeId,
    value: Value,
}
struct Frame {
    has_async_actions: bool,
    compilation: Arc<Compilation>,
    session: ExecutionSession,
    results: Results,
    conditions: Vec<ConditionOutcome>,
    status: SessionStatus,
    started: bool,
    updates: Updates,
    parent: Option<Parent>,
    prefix: Vec<usize>,
    subgraphs: HashMap<NodeId, ExecutionOutcome>,
}
impl Frame {
    fn new(
        compilation: Arc<Compilation>,
        skipped: Vec<NodeId>,
        parent: Option<Parent>,
        prefix: Vec<usize>,
        admission: Option<rivlet::SessionConcurrency>,
    ) -> Result<Self, ExecutionError> {
        let session = ExecutionSession::new(Arc::new(compilation.graph().clone()), skipped)?;
        let session = match admission {
            None => session,
            Some(binding) => session.with_concurrency(binding)?,
        };
        Ok(Self {
            has_async_actions: (0..compilation.graph().nodes().len()).any(|index| matches!(compilation.node(NodeId::new(index)).kind(),ProjectedKind::Step(action) if action.is_async())),
            session,
            results: Results::new(compilation.graph().nodes().len()),
            conditions: (0..compilation.graph().conditions().len())
                .map(|_| ConditionOutcome::NotEvaluated)
                .collect(),
            compilation,
            status: SessionStatus::Running,
            started: false,
            updates: Updates::default(),
            parent,
            prefix,
            subgraphs: HashMap::new(),
        })
    }
    fn outcome(self) -> Result<ExecutionOutcome, ExecutionError> {
        let status = match self.status {
            SessionStatus::Finished => ExecutionStatus::Succeeded,
            SessionStatus::Failed => ExecutionStatus::Failed,
            SessionStatus::Cancelled => ExecutionStatus::Cancelled,
            SessionStatus::Running => return Err(ExecutionError::Stalled),
        };
        Ok(ExecutionOutcome::new(
            self.compilation,
            self.results.finish()?,
            self.conditions,
            status,
        )
        .with_subgraphs(self.subgraphs))
    }
}

pub(super) struct State {
    frames: Vec<Option<Frame>>,
    queue: VecDeque<usize>,
    queued: Vec<bool>,
    active: BTreeSet<usize>,
    jobs: HashMap<usize, (usize, NodeId)>,
    next_job: usize,
    condition_sites: Vec<(usize, ConditionId)>,
    pub(super) workers: PoolOwner,
    limit: usize,
    running: usize,
    pub(super) cancellation: CancellationToken,
    cancellation_forwarded: bool,
    events: Option<Arc<Observations>>,
    _observer_link: Option<CancellationRegistration>,
    mode: Mode,
    admission: crate::admission::Admission,
    pending_sync: VecDeque<ActionSite>,
    pending_async: Vec<(ActionSite, ActionFuture)>,
}
impl State {
    pub(super) fn new(
        compilation: Arc<Compilation>,
        options: ExecutionOptions,
        workers: PoolOwner,
        mode: Mode,
    ) -> Result<Self, ExecutionError> {
        let (admission, binding) =
            crate::admission::Admission::resolve(&compilation, &options.limits)?;
        let events = options.events.map(Observations::new);
        let (cancellation, link) = if let Some(events) = &events {
            let forward = Arc::clone(events);
            let link = options.cancellation.subscribe(move || forward.cancel());
            (events.token(), Some(link))
        } else {
            (options.cancellation, None)
        };
        Ok(Self {
            frames: vec![Some(Frame::new(
                compilation,
                options.skipped,
                None,
                Vec::new(),
                binding,
            )?)],
            queue: VecDeque::from([0]),
            queued: vec![true],
            active: BTreeSet::from([0]),
            jobs: HashMap::new(),
            next_job: 0,
            condition_sites: Vec::new(),
            workers,
            limit: options.workers.get(),
            running: 0,
            cancellation,
            cancellation_forwarded: false,
            events,
            _observer_link: link,
            mode,
            admission,
            pending_sync: VecDeque::new(),
            pending_async: Vec::new(),
        })
    }
    fn frame(&self, id: usize) -> &Frame {
        self.frames[id].as_ref().expect("active session")
    }
    fn frame_mut(&mut self, id: usize) -> &mut Frame {
        self.frames[id].as_mut().expect("active session")
    }
    fn schedule(&mut self, id: usize) {
        if self.frames[id]
            .as_ref()
            .is_some_and(|frame| frame.status == SessionStatus::Running)
            && !self.queued[id]
        {
            self.queue.push_back(id);
            self.queued[id] = true;
        }
    }
    fn schedule_all(&mut self) {
        for id in self.active.iter().copied().collect::<Vec<_>>() {
            self.schedule(id);
        }
    }
    pub(super) fn cancellation_pending(&self) -> bool {
        self.cancellation.is_cancelled() && !self.cancellation_forwarded
    }
    pub(super) fn is_finished(&self) -> bool {
        self.frame(0).status != SessionStatus::Running
    }
    fn emit(&self, frame: usize, node: NodeId, detail: NodeEventDetail) {
        if let Some(events) = &self.events {
            let frame = self.frame(frame);
            events.emit(frame.compilation.node(node), &frame.prefix, detail);
        }
    }
    pub(super) fn dispatch(&mut self) -> Result<Vec<ConditionWork>, ExecutionError> {
        self.dispatch_queued()?;
        if self.cancellation_pending() {
            self.schedule_all();
            self.cancellation_forwarded = true;
        }
        let mut work = Vec::new();
        let mut deferred = Vec::new();
        while let Some(id) = self.queue.pop_front() {
            self.queued[id] = false;
            let capacity = if self.mode == Mode::Async && self.frame(id).has_async_actions {
                self.frame(id).compilation.graph().nodes().len().max(1)
            } else {
                self.limit - self.running
            };
            // start 要求至少一个名额；只延后新会话，不借用额外工作槽。
            if capacity == 0 && !self.frame(id).started {
                deferred.push(id);
                continue;
            }
            let cancelling = self.cancellation.is_cancelled();
            let frame = self.frame_mut(id);
            if cancelling {
                frame.session.request_cancel();
            }
            let batch = if frame.started {
                let updates = std::mem::take(&mut frame.updates);
                frame.session.advance(
                    ExecutionUpdate::new(updates.completed, updates.conditions, [])
                        .cancelled(updates.cancelled),
                    capacity,
                )?
            } else {
                frame.started = true;
                frame.session.start(capacity)?
            };
            frame.status = batch.status();
            frame.results.events(batch.events())?;
            self.emit_batch(id, batch.events());
            self.dispatch_actions(id, batch.actions())?;
            work.extend(self.dispatch_conditions(
                id,
                batch.conditions().iter().map(|request| request.condition()),
            )?);
            if batch.status() != SessionStatus::Running && id != 0 {
                self.complete_subgraph(id)?;
            }
        }
        for id in deferred {
            self.schedule(id);
        }
        Ok(work)
    }

    fn emit_batch(&self, id: usize, events: &[SchedulerEvent]) {
        if self.events.is_some() {
            for event in events {
                match *event {
                    SchedulerEvent::CompositeEntered(node) => {
                        self.emit(id, node, NodeEventDetail::Started);
                    }
                    SchedulerEvent::CompositeCompleted(node) => {
                        self.emit(id, node, NodeEventDetail::Completed);
                    }
                    SchedulerEvent::NodeSkipped(node) => {
                        self.emit(id, node, NodeEventDetail::Skipped);
                    }
                    SchedulerEvent::NodeCancelled(node) => {
                        self.emit(id, node, NodeEventDetail::Cancelled);
                    }
                    SchedulerEvent::NodeBlocked { node, cause } => {
                        self.emit(id, node, NodeEventDetail::Blocked(cause));
                    }
                    SchedulerEvent::NodeFailed {
                        node,
                        cause: FailureCause::Child(child),
                    } => self.emit(id, node, NodeEventDetail::Failed(NodeFailure::Child(child))),
                    SchedulerEvent::NodeFailed {
                        cause: FailureCause::Action,
                        ..
                    }
                    | SchedulerEvent::CancellationRequested
                    | SchedulerEvent::ConditionSkipped(_)
                    | SchedulerEvent::ConditionEvaluated { .. } => (),
                }
            }
        }
    }

    fn dispatch_actions(&mut self, id: usize, actions: &[NodeId]) -> Result<(), ExecutionError> {
        for node in actions {
            if self.cancellation.is_cancelled() {
                self.frame_mut(id).updates.cancelled.push(*node);
                self.schedule(id);
                continue;
            }
            let projected = self.frame(id).compilation.node(*node);
            let ProjectedKind::Step(action) = projected.kind() else {
                return Err(ExecutionError::UnexpectedComposite(*node));
            };
            let action = action.clone();
            if !action.is_async() && self.running == self.limit {
                self.pending_sync.push_back(ActionSite {
                    frame: id,
                    node: *node,
                });
                continue;
            }
            let inputs = projected
                .inputs()
                .iter()
                .map(|source| self.frame(id).results.input(*source))
                .collect::<Result<Vec<_>, _>>()?;
            self.emit(id, *node, NodeEventDetail::Started);
            if self.cancellation.is_cancelled() {
                self.frame_mut(id).updates.cancelled.push(*node);
                self.schedule(id);
                continue;
            }
            if action.is_async() {
                let site = ActionSite {
                    frame: id,
                    node: *node,
                };
                match action.start_async(
                    ActionContext::default().with_cancellation(self.cancellation.clone()),
                    &inputs,
                ) {
                    Ok(future) => self.pending_async.push((site, future)),
                    Err(error) => self.returned(id, *node, Err(error))?,
                }
                continue;
            }
            let job = self.next_job;
            self.next_job = job.checked_add(1).expect("job identity overflow");
            self.workers
                .executor(self.limit)
                .map_err(ExecutionError::Executor)?
                .submit(
                    job,
                    action,
                    ActionContext::default().with_cancellation(self.cancellation.clone()),
                    inputs,
                )
                .map_err(ExecutionError::Executor)?;
            self.jobs.insert(job, (id, *node));
            self.running += 1;
        }
        Ok(())
    }

    fn dispatch_queued(&mut self) -> Result<(), ExecutionError> {
        while self.running < self.limit || self.cancellation.is_cancelled() {
            let Some(site) = self.pending_sync.pop_front() else {
                break;
            };
            self.dispatch_actions(site.frame, &[site.node])?;
        }
        Ok(())
    }
    pub(super) fn take_async_actions(&mut self) -> Vec<(ActionSite, ActionFuture)> {
        std::mem::take(&mut self.pending_async)
    }
    pub(super) fn complete_async(
        &mut self,
        site: ActionSite,
        result: Result<Value, ActionError>,
    ) -> Result<(), ExecutionError> {
        self.returned(site.frame, site.node, result)?;
        self.schedule_all();
        Ok(())
    }

    fn dispatch_conditions(
        &mut self,
        id: usize,
        conditions: impl Iterator<Item = ConditionId>,
    ) -> Result<Vec<ConditionWork>, ExecutionError> {
        let mut work = Vec::new();
        for local in conditions {
            let global = ConditionId::new(self.condition_sites.len());
            self.condition_sites.push((id, local));
            let evaluation = if self.cancellation.is_cancelled() {
                ConditionEvaluation::Ready(Err(ActionError::Cancelled))
            } else {
                let frame = self.frame(id);
                let condition = frame.compilation.condition(local);
                let inputs = condition
                    .inputs()
                    .iter()
                    .map(|source| frame.results.input(*source))
                    .collect::<Result<Vec<_>, _>>()?;
                condition.start(&inputs)
            };
            work.push((global, evaluation));
        }
        Ok(work)
    }

    fn complete_subgraph(&mut self, id: usize) -> Result<(), ExecutionError> {
        self.active.remove(&id);
        let mut frame = self.frames[id].take().expect("completed child session");
        let parent = frame.parent.take().expect("child session parent");
        let outcome = frame.outcome()?;
        let result = match outcome.status() {
            ExecutionStatus::Succeeded => Ok(parent.value),
            ExecutionStatus::Failed => Err(NodeFailure::Subgraph),
            ExecutionStatus::Cancelled => {
                self.frame_mut(parent.frame)
                    .updates
                    .cancelled
                    .push(parent.node);
                self.schedule(parent.frame);
                self.frame_mut(parent.frame)
                    .subgraphs
                    .insert(parent.node, outcome);
                return Ok(());
            }
        };
        self.frame_mut(parent.frame)
            .subgraphs
            .insert(parent.node, outcome);
        self.complete(parent.frame, parent.node, result)?;
        Ok(())
    }
    pub(super) fn resolve(
        &mut self,
        id: ConditionId,
        result: Result<bool, ActionError>,
    ) -> Resolution {
        let (frame, local) = self.condition_sites[id.index()];
        let (outcome, value) = match result {
            Ok(value) => (
                ConditionOutcome::Resolved(value),
                ConditionResolution::new(local, value),
            ),
            Err(ActionError::Cancelled) if self.cancellation.is_cancelled() => (
                ConditionOutcome::Cancelled,
                ConditionResolution::cancelled(local),
            ),
            Err(error) => (
                ConditionOutcome::Failed(error),
                ConditionResolution::failed(local),
            ),
        };
        self.frame_mut(frame).conditions[local.index()] = outcome;
        Resolution { frame, value }
    }
    pub(super) const fn actions_running(&self) -> bool {
        self.running > 0
    }
    fn complete(
        &mut self,
        frame: usize,
        node: NodeId,
        result: Result<Value, NodeFailure>,
    ) -> Result<(), ExecutionError> {
        let (outcome, status, event) = match result {
            Ok(value) => (
                NodeOutcome::Succeeded(value),
                Ok(()),
                NodeEventDetail::Completed,
            ),
            Err(failure) => (
                NodeOutcome::Failed(failure.clone()),
                Err(()),
                NodeEventDetail::Failed(failure),
            ),
        };
        self.frame_mut(frame).results.record(node, outcome)?;
        self.emit(frame, node, event);
        self.frame_mut(frame).updates.completed.push((node, status));
        self.schedule(frame);
        Ok(())
    }
    pub(super) fn complete_actions(&mut self) -> Result<(), ExecutionError> {
        for completion in self.workers.drain() {
            let (frame, node) = self
                .jobs
                .remove(&completion.node())
                .ok_or(ExecutionError::InvalidCompletion)?;
            self.running = self
                .running
                .checked_sub(1)
                .ok_or(ExecutionError::InvalidCompletion)?;
            self.returned(frame, node, completion.into_result())?;
        }
        self.schedule_all();
        Ok(())
    }

    fn returned(
        &mut self,
        frame: usize,
        node: NodeId,
        result: Result<Value, ActionError>,
    ) -> Result<(), ExecutionError> {
        match result {
            Ok(value) if value.type_id() == ValueType::of::<Graph>().id() => {
                let graph = value
                    .downcast::<Graph>()
                    .map_err(ExecutionError::Executor)?;
                let compilation = match graph.compile() {
                    Ok(compilation) => Arc::new(compilation),
                    Err(error) => {
                        self.complete(frame, node, Err(NodeFailure::Expansion(error)))?;
                        return Ok(());
                    }
                };
                if self.mode == Mode::Sync && super::check_sync(&compilation).is_err() {
                    self.complete(frame, node, Err(NodeFailure::AsyncSubgraph))?;
                    return Ok(());
                }
                let mut prefix = self.frame(frame).prefix.clone();
                prefix.extend(self.frame(frame).compilation.node(node).path());
                let inherited = self.frame_mut(frame).session.suspend_action(node)?;
                let binding = self
                    .admission
                    .child(compilation.graph().nodes().len(), &inherited);
                let child = Frame::new(
                    compilation,
                    Vec::new(),
                    Some(Parent { frame, node, value }),
                    prefix,
                    binding,
                )?;
                let id = self.frames.len();
                self.frames.push(Some(child));
                self.queued.push(false);
                self.active.insert(id);
                self.schedule(id);
            }
            Ok(value) => self.complete(frame, node, Ok(value))?,
            Err(ActionError::Cancelled) if self.cancellation.is_cancelled() => {
                self.frame_mut(frame).updates.cancelled.push(node);
                self.schedule(frame);
            }
            Err(error) => self.complete(frame, node, Err(NodeFailure::Action(error)))?,
        }
        Ok(())
    }
    pub(super) fn advance(&mut self, conditions: Vec<Resolution>) {
        for resolution in conditions {
            self.frame_mut(resolution.frame)
                .updates
                .conditions
                .push(resolution.value);
            self.schedule(resolution.frame);
        }
        if self.cancellation_pending() {
            self.schedule_all();
        }
    }
    pub(super) fn finish(
        mut self,
    ) -> Result<(ExecutionOutcome, PoolOwner, Option<ActionError>), ExecutionError> {
        let outcome = self.frames[0].take().expect("root session").outcome()?;
        Ok((
            outcome,
            self.workers,
            self.events.as_ref().and_then(|events| events.error()),
        ))
    }
}
