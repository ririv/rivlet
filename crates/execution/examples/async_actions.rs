use action::{ActionContext, ActionError, ActionHandle, AsyncAction};
use execution::{Execution, ExecutionOptions, ExecutionStatus};
use graph::{Edge, Graph, Step};
use std::sync::Arc;

struct Fetch;
impl AsyncAction for Fetch {
    type Inputs = ();
    type Output = usize;
    async fn execute(&self, context: ActionContext, (): ()) -> Result<usize, ActionError> {
        context.control().require_running()?;
        // 使用者选择 I/O 实现与运行环境；这个示例使用现有 dev 依赖的计时器。
        tokio::time::sleep(std::time::Duration::from_millis(1)).await;
        Ok(21)
    }
}
struct Apply;
impl AsyncAction for Apply {
    type Inputs = (Option<Arc<usize>>,);
    type Output = usize;
    async fn execute(
        &self,
        _: ActionContext,
        (value,): Self::Inputs,
    ) -> Result<usize, ActionError> {
        Ok(*value.ok_or_else(|| ActionError::new("missing value"))? * 2)
    }
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let fetch = Step::new("fetch", ActionHandle::new_async(Fetch))?;
    let apply = Step::new("apply", ActionHandle::new_async(Apply))?;
    let workflow = Graph::new(
        "workflow",
        [fetch.clone().into(), apply.clone().into()],
        [Edge::results([&fetch], &apply)?],
    )?;
    let options = ExecutionOptions::new(4)?.max_concurrency(&workflow, 2)?;
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_time()
        .build()?;
    let outcome = runtime.block_on(Execution::new(workflow.compile()?, options).run_async())?;
    assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
    let result = outcome
        .nodes()
        .find_map(|(id, value)| {
            if outcome.compilation().node(id).identity() != apply.id() {
                return None;
            }
            match value {
                execution::NodeOutcome::Succeeded(value) => Some(value),
                _ => None,
            }
        })
        .ok_or("missing apply result")?;
    println!("{}", result.downcast::<usize>()?);
    Ok(())
}
