use std::sync::Arc;

use action::{Action, ActionContext, ActionError, ActionHandle};
use execution::{ConditionOutcome, Execution, ExecutionOptions, ExecutionStatus, NodeOutcome};
use graph::{Condition, Edge, Graph, Step};
use rivlet::NodeId;

struct Number(i64);
impl Action for Number {
    type Inputs = ();
    type Output = i64;
    fn execute(&self, _: &ActionContext, (): ()) -> Result<i64, ActionError> {
        Ok(self.0)
    }
}

struct Double;
impl Action for Double {
    type Inputs = (Option<Arc<i64>>,);
    type Output = i64;
    fn execute(&self, _: &ActionContext, (plan,): Self::Inputs) -> Result<i64, ActionError> {
        plan.map(|value| *value * 2)
            .ok_or_else(|| ActionError::new("plan is missing"))
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let enabled = Step::new("enabled", ActionHandle::new(Number(1)))?;
    let plan = Step::new("plan", ActionHandle::new(Number(21)))?;
    let approved = Condition::new_async(
        |(value,): (Option<Arc<i64>>,)| async move {
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
            Ok(value.is_some_and(|value| *value == 1))
        },
        [(&enabled).into()],
    )?;
    let apply = Step::with_condition("apply", ActionHandle::new(Double), approved)?;
    let workflow = Graph::new(
        "workflow",
        [enabled.into(), plan.clone().into(), apply.clone().into()],
        [Edge::results([&plan], &apply)?],
    )?;
    let compilation = workflow.compile()?;
    // 此例只挂载一次 apply；重复挂载时应按编译路径选择对应实例。
    let applied = (0..compilation.graph().nodes().len())
        .map(NodeId::new)
        .find(|node| compilation.node(*node).identity() == apply.id())
        .ok_or("apply was not compiled")?;
    let condition = compilation.graph().nodes()[applied.index()]
        .condition()
        .ok_or("apply has no condition")?;
    // 应用显式提供运行时，Rivlet 不自动创建 Tokio 环境。
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_time()
        .build()?;
    let outcome =
        runtime.block_on(Execution::new(compilation, ExecutionOptions::new(2)?).run_async())?;
    assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
    let Some(ConditionOutcome::Resolved(approved)) = outcome.condition(condition) else {
        return Err("predicate did not resolve".into());
    };
    let Some(NodeOutcome::Succeeded(value)) = outcome.node(applied) else {
        return Err("apply did not succeed".into());
    };
    println!("condition: {approved}");
    println!("apply: {}", value.downcast::<i64>()?);
    Ok(())
}
