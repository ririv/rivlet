use action::{Action, ActionContext, ActionError, ActionHandle, AsyncAction};
use execution::{
    CancellationSource, Execution, ExecutionError, ExecutionOptions, ExecutionStatus, NodeFailure,
    NodeOutcome,
};
use graph::{Condition, Edge, Graph, Step};
use rivlet::NodeId;
use std::future::{Future, poll_fn};
use std::pin::Pin;
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering},
};
use std::task::Poll;
use std::time::Duration;

fn run<F: Future>(future: F) -> F::Output {
    tokio::runtime::Builder::new_current_thread()
        .enable_time()
        .build()
        .unwrap()
        .block_on(async {
            tokio::time::timeout(Duration::from_secs(3), future)
                .await
                .expect("execution stalled")
        })
}
fn send<F: Future + Send>(future: F) -> F {
    future
}
struct Number;
impl AsyncAction for Number {
    type Inputs = ();
    type Output = usize;
    async fn execute(&self, _: ActionContext, (): ()) -> Result<usize, ActionError> {
        tokio::time::sleep(Duration::from_millis(1)).await;
        Ok(21)
    }
}
struct Double;
impl Action for Double {
    type Inputs = (Option<Arc<usize>>,);
    type Output = usize;
    fn execute(&self, _: &ActionContext, (value,): Self::Inputs) -> Result<usize, ActionError> {
        Ok(*value.ok_or_else(|| ActionError::new("missing number"))? * 2)
    }
}
struct AsyncDouble;
impl AsyncAction for AsyncDouble {
    type Inputs = (Option<Arc<usize>>,);
    type Output = usize;
    async fn execute(
        &self,
        _: ActionContext,
        (value,): Self::Inputs,
    ) -> Result<usize, ActionError> {
        Ok(*value.ok_or_else(|| ActionError::new("missing number"))? * 2)
    }
}
#[test]
fn mixed_actions_conditions_and_typed_results_share_one_driver() {
    let number = Step::new("number", ActionHandle::new_async(Number)).unwrap();
    let double = Step::new("double", ActionHandle::new(Double)).unwrap();
    let check=Condition::new_async(|(value,):(Option<Arc<usize>>,)|async move{Ok(value.is_some_and(|value|*value==42))},[(&double).into()]).unwrap();
    let final_step =
        Step::with_condition("final", ActionHandle::new_async(AsyncDouble), check).unwrap();
    let graph = Graph::new(
        "root",
        [
            number.clone().into(),
            double.clone().into(),
            final_step.clone().into(),
        ],
        [
            Edge::results([&number], &double).unwrap(),
            Edge::results([&double], &final_step).unwrap(),
        ],
    )
    .unwrap();
    let compilation = graph.compile().unwrap();
    assert!(matches!(
        Execution::new(graph.compile().unwrap(), ExecutionOptions::new(1).unwrap()).run(),
        Err(ExecutionError::AsyncAction(_))
    ));
    let outcome = run(send(
        Execution::new(compilation, ExecutionOptions::new(1).unwrap()).run_async(),
    ))
    .unwrap();
    assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
    let Some(NodeOutcome::Succeeded(value)) = outcome.node(NodeId::new(3)) else {
        panic!("missing result");
    };
    assert_eq!(*value.downcast::<usize>().unwrap(), 84);
}

struct Meet(Arc<tokio::sync::Barrier>);
impl AsyncAction for Meet {
    type Inputs = ();
    type Output = std::thread::ThreadId;
    async fn execute(&self, _: ActionContext, (): ()) -> Result<Self::Output, ActionError> {
        self.0.wait().await;
        Ok(std::thread::current().id())
    }
}
#[test]
fn asynchronous_actions_do_not_consume_blocking_workers_or_require_a_tokio_dependency() {
    let barrier = Arc::new(tokio::sync::Barrier::new(3));
    let nodes = (0..3).map(|_| {
        Step::new("meet", ActionHandle::new_async(Meet(Arc::clone(&barrier))))
            .unwrap()
            .into()
    });
    let graph = Graph::new("root", nodes, []).unwrap();
    let driver_thread = std::thread::current().id();
    let outcome = run(
        Execution::new(graph.compile().unwrap(), ExecutionOptions::new(1).unwrap()).run_async(),
    )
    .unwrap();
    for (_, node) in outcome.nodes().skip(1) {
        let NodeOutcome::Succeeded(value) = node else {
            panic!("failed meeting");
        };
        assert_eq!(
            *value.downcast::<std::thread::ThreadId>().unwrap(),
            driver_thread
        );
    }
}

struct Active(Arc<AtomicUsize>, Arc<AtomicUsize>);
impl Action for Active {
    type Inputs=();type Output=();
    fn execute(&self,_:&ActionContext,():())->Result<(),ActionError>{
        let active=self.0.fetch_add(1,Ordering::SeqCst)+1;self.1.fetch_max(active,Ordering::SeqCst);
        std::thread::sleep(Duration::from_millis(2));self.0.fetch_sub(1,Ordering::SeqCst);Ok(())
    }
}
impl AsyncAction for Active {
    type Inputs = ();
    type Output = ();
    async fn execute(&self, _: ActionContext, (): ()) -> Result<(), ActionError> {
        let active = self.0.fetch_add(1, Ordering::SeqCst) + 1;
        self.1.fetch_max(active, Ordering::SeqCst);
        tokio::time::sleep(Duration::from_millis(2)).await;
        self.0.fetch_sub(1, Ordering::SeqCst);
        Ok(())
    }
}
#[test]
fn explicit_node_limits_bound_async_actions_independently_of_worker_count() {
    let active = Arc::new(AtomicUsize::new(0));
    let peak = Arc::new(AtomicUsize::new(0));
    let nodes = (0..8).map(|_| {
        Step::new(
            "active",
            ActionHandle::new_async(Active(Arc::clone(&active), Arc::clone(&peak))),
        )
        .unwrap()
        .into()
    });
    let graph = Graph::new("root", nodes, []).unwrap();
    let options = ExecutionOptions::new(1)
        .unwrap()
        .max_concurrency(&graph, 2)
        .unwrap();
    let outcome = run(Execution::new(graph.compile().unwrap(), options).run_async()).unwrap();
    assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
    assert_eq!(peak.load(Ordering::SeqCst), 2);
}

#[test]
fn the_same_scope_budget_counts_blocking_and_async_actions_together(){
    let active=Arc::new(AtomicUsize::new(0));let peak=Arc::new(AtomicUsize::new(0));
    let nodes=(0..16).map(|index|{
        let work=Active(Arc::clone(&active),Arc::clone(&peak));
        let handle=if index%2==0{ActionHandle::new(work)}else{ActionHandle::new_async(work)};
        Step::new("mixed",handle).unwrap().into()
    });
    let graph=Graph::new("root",nodes,[]).unwrap();
    let options=ExecutionOptions::new(4).unwrap().max_concurrency(&graph,2).unwrap();
    let outcome=run(Execution::new(graph.compile().unwrap(),options).run_async()).unwrap();
    assert_eq!(outcome.status(),ExecutionStatus::Succeeded);assert!(peak.load(Ordering::SeqCst)<=2);
}

struct Ready;
impl AsyncAction for Ready {
    type Inputs=();type Output=usize;
    async fn execute(&self,_:ActionContext,():())->Result<usize,ActionError>{Ok(7)}
}
#[test]
fn runtime_independent_actions_can_complete_with_only_a_standard_library_poller(){
    let node=Step::new("ready",ActionHandle::new_async(Ready)).unwrap();
    let graph=Graph::new("root",[node.into()],[]).unwrap();
    let mut future=Box::pin(Execution::new(graph.compile().unwrap(),ExecutionOptions::new(1).unwrap()).run_async());
    let mut context=std::task::Context::from_waker(std::task::Waker::noop());
    let Poll::Ready(outcome)=future.as_mut().poll(&mut context)else{panic!("ready actions need no external runtime");};
    assert_eq!(outcome.unwrap().status(),ExecutionStatus::Succeeded);
}

struct Generate(Graph);
impl AsyncAction for Generate {
    type Inputs = ();
    type Output = Graph;
    async fn execute(&self, _: ActionContext, (): ()) -> Result<Graph, ActionError> {
        Ok(self.0.clone())
    }
}
#[test]
fn asynchronous_generators_release_one_slot_and_children_inherit_its_limit() {
    let graph = Graph::new(
        "child",
        [Step::new("number", ActionHandle::new_async(Number))
            .unwrap()
            .into()],
        [],
    )
    .unwrap();
    let generate = Step::new("generate", ActionHandle::new_async(Generate(graph))).unwrap();
    let root = Graph::new("root", [generate.into()], []).unwrap();
    let options = ExecutionOptions::new(1)
        .unwrap()
        .max_concurrency(&root, 1)
        .unwrap();
    let outcome = run(Execution::new(root.compile().unwrap(), options).run_async()).unwrap();
    assert_eq!(
        outcome.subgraph(NodeId::new(1)).unwrap().status(),
        ExecutionStatus::Succeeded
    );
}

struct WaitCancel(Arc<tokio::sync::Notify>);
impl AsyncAction for WaitCancel {
    type Inputs = ();
    type Output = ();
    async fn execute(&self, ctx: ActionContext, (): ()) -> Result<(), ActionError> {
        self.0.notify_one();
        ctx.control().cancelled().await;
        ctx.control().require_running()
    }
}
#[test]
fn explicit_cancel_keeps_polling_running_actions_until_they_acknowledge() {
    let source = CancellationSource::new();
    let entered = Arc::new(tokio::sync::Notify::new());
    let work = Step::new(
        "waiting",
        ActionHandle::new_async(WaitCancel(Arc::clone(&entered))),
    )
    .unwrap();
    let graph = Graph::new("root", [work.into()], []).unwrap();
    let future = Execution::new(
        graph.compile().unwrap(),
        ExecutionOptions::new(1)
            .unwrap()
            .cancellation(source.token()),
    )
    .run_async();
    let (outcome, ()) = run(async {
        tokio::join!(send(future), async {
            entered.notified().await;
            source.cancel();
        })
    });
    assert_eq!(outcome.unwrap().status(), ExecutionStatus::Cancelled);
}

#[derive(Clone, Copy)]
enum PanicAt {
    Factory,
    Poll,
}
struct Panics(PanicAt);
impl AsyncAction for Panics {
    type Inputs = ();
    type Output = ();
    fn execute(
        &self,
        _: ActionContext,
        (): (),
    ) -> impl Future<Output = Result<(), ActionError>> + Send {
        assert!(!matches!(self.0, PanicAt::Factory), "factory panic");
        let stage = self.0;
        async move {
            assert!(!matches!(stage, PanicAt::Poll), "poll panic");
            Ok(())
        }
    }
}
#[test]
fn asynchronous_factory_and_poll_panics_fail_only_related_nodes() {
    for stage in [PanicAt::Factory, PanicAt::Poll] {
        let bad = Step::new("bad", ActionHandle::new_async(Panics(stage))).unwrap();
        let good = Step::new("good", ActionHandle::new_async(Number)).unwrap();
        let graph = Graph::new("root", [bad.into(), good.into()], []).unwrap();
        let outcome = run(Execution::new(
            graph.compile().unwrap(),
            ExecutionOptions::new(1).unwrap(),
        )
        .run_async())
        .unwrap();
        assert!(matches!(
            outcome.node(NodeId::new(1)),
            Some(NodeOutcome::Failed(NodeFailure::Action(_)))
        ));
        assert!(matches!(
            outcome.node(NodeId::new(2)),
            Some(NodeOutcome::Succeeded(_))
        ));
    }
}

struct DropCount(Arc<AtomicUsize>);
impl Drop for DropCount {
    fn drop(&mut self) {
        self.0.fetch_add(1, Ordering::SeqCst);
    }
}
struct Forever(Arc<AtomicUsize>);
impl AsyncAction for Forever {
    type Inputs = ();
    type Output = ();
    async fn execute(&self, _: ActionContext, (): ()) -> Result<(), ActionError> {
        let _guard = DropCount(Arc::clone(&self.0));
        std::future::pending().await
    }
}
#[test]
fn dropping_the_execution_future_drops_pending_action_futures_without_blocking() {
    let dropped = Arc::new(AtomicUsize::new(0));
    let node = Step::new(
        "forever",
        ActionHandle::new_async(Forever(Arc::clone(&dropped))),
    )
    .unwrap();
    let graph = Graph::new("root", [node.into()], []).unwrap();
    let mut future = Box::pin(
        Execution::new(graph.compile().unwrap(), ExecutionOptions::new(1).unwrap()).run_async(),
    );
    run(poll_fn(|cx| {
        assert!(Pin::new(&mut future).poll(cx).is_pending());
        Poll::Ready(())
    }));
    drop(future);
    assert_eq!(dropped.load(Ordering::SeqCst), 1);
}

struct IgnoreCancel {
    entered: Arc<tokio::sync::Notify>,
    release: Arc<tokio::sync::Notify>,
}
impl AsyncAction for IgnoreCancel {
    type Inputs = ();
    type Output = usize;
    async fn execute(&self, _: ActionContext, (): ()) -> Result<usize, ActionError> {
        self.entered.notify_one();
        self.release.notified().await;
        Ok(42)
    }
}
#[test]
fn cancellation_drains_uncooperative_futures_and_retains_their_real_success() {
    let source = CancellationSource::new();
    let entered = Arc::new(tokio::sync::Notify::new());
    let release = Arc::new(tokio::sync::Notify::new());
    let work = Step::new(
        "ignore",
        ActionHandle::new_async(IgnoreCancel {
            entered: Arc::clone(&entered),
            release: Arc::clone(&release),
        }),
    )
    .unwrap();
    let tail = Step::new("tail", ActionHandle::new_async(Number)).unwrap();
    let graph = Graph::new(
        "root",
        [work.clone().into(), tail.clone().into()],
        [Edge::new(&work, &tail)],
    )
    .unwrap();
    let execution = Execution::new(
        graph.compile().unwrap(),
        ExecutionOptions::new(1)
            .unwrap()
            .cancellation(source.token()),
    );
    let (outcome, ()) = run(async {
        tokio::join!(execution.run_async(), async {
            entered.notified().await;
            source.cancel();
            tokio::task::yield_now().await;
            release.notify_one();
        })
    });
    let outcome = outcome.unwrap();
    assert_eq!(outcome.status(), ExecutionStatus::Cancelled);
    let Some(NodeOutcome::Succeeded(value)) = outcome.node(NodeId::new(1)) else {
        panic!("real success lost");
    };
    assert_eq!(*value.downcast::<usize>().unwrap(), 42);
    assert!(matches!(
        outcome.node(NodeId::new(2)),
        Some(NodeOutcome::Cancelled)
    ));
}
