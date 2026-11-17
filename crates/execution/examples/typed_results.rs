use std::error::Error;
use std::sync::Arc;

use action::{Action, ActionContext, ActionError, ActionHandle};
use execution::{Execution, ExecutionOptions, ExecutionStatus};
use graph::{Edge, Graph, Step};

// 故意不实现 Clone，验证跨节点共享原始 Rust 结果，而不是复制载荷。
struct Plan {
    count: usize,
}

struct Design;
impl Action for Design {
    type Inputs = ();
    type Output = Plan;

    fn execute(&self, _: &ActionContext, (): ()) -> Result<Plan, ActionError> {
        Ok(Plan { count: 21 })
    }
}

struct Apply;
impl Action for Apply {
    type Inputs = (Option<Arc<Plan>>,);
    type Output = usize;

    fn execute(&self, _: &ActionContext, (plan,): Self::Inputs) -> Result<usize, ActionError> {
        let plan = plan.ok_or_else(|| ActionError::new("plan was skipped"))?;
        Ok(plan.count * 2)
    }
}

fn main() -> Result<(), Box<dyn Error>> {
    let design = Step::new("design", ActionHandle::new(Design))?;
    let apply = Step::new("apply", ActionHandle::new(Apply))?;
    let edge = Edge::results([&design], &apply)?;
    let graph = Graph::new("workflow", [design.into(), apply.clone().into()], [edge])?;
    let compilation = graph.compile()?;
    let outcome = Execution::new(compilation, ExecutionOptions::new(4)?).run()?;

    if outcome.status() == ExecutionStatus::Failed {
        return Err("workflow failed".into());
    }
    println!(
        "apply: {}",
        outcome
            .result::<usize>(&apply)?
            .ok_or("apply was skipped")?
    );
    Ok(())
}
