use std::error::Error;
use std::sync::Arc;

use action::{Action, ActionContext, ActionError, ActionHandle};
use execution::{Execution, ExecutionOptions, ExecutionStatus, NodeOutcome};
use graph::{Edge, Graph, GraphStructure, Leaf};

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
    let design = Leaf::new("design", ActionHandle::new(Design))?;
    let apply = Leaf::new("apply", ActionHandle::new(Apply))?;
    let edge = Edge::results([&design], &apply)?;
    let graph = Graph::new(
        "workflow",
        Arc::new(GraphStructure::new([design.into(), apply.into()], [edge])?),
    )?;
    let compilation = graph.compile()?;
    let outcome = Execution::new(compilation, ExecutionOptions::new(4)?).run()?;

    for (node, result) in outcome.nodes() {
        let metadata = outcome.compilation().node(node);
        match result {
            NodeOutcome::Succeeded(value) if value.type_id() == std::any::TypeId::of::<usize>() => {
                println!("{}: {}", metadata.label(), value.downcast::<usize>()?);
            }
            other => println!("{}: {other:?}", metadata.label()),
        }
    }
    if outcome.status() == ExecutionStatus::Failed {
        return Err("workflow failed".into());
    }
    Ok(())
}
