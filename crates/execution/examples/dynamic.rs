use action::{Action, ActionContext, ActionError, ActionHandle};
use execution::{Execution, ExecutionOptions, NodeOutcome};
use graph::{Graph, Step};
use rivlet::NodeId;
use std::sync::Arc;

struct Decode;
impl Action for Decode {
    type Inputs = ();
    type Output = usize;
    fn execute(&self, _: &ActionContext, (): ()) -> Result<usize, ActionError> {
        Ok(42)
    }
}
struct Prepare(Graph);
impl Action for Prepare {
    type Inputs = ();
    type Output = Graph;
    fn execute(&self, _: &ActionContext, (): ()) -> Result<Graph, ActionError> {
        Ok(self.0.clone())
    }
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let decode = Step::new("decode", ActionHandle::new(Decode))?;
    let generated = Graph::new("generated", [decode.into()], [])?;
    let prepare = Step::new("prepare", ActionHandle::new(Prepare(generated)))?;
    let compilation = Arc::new(Graph::new("workflow", [prepare.into()], [])?.compile()?);
    let outcome = Execution::new(compilation, ExecutionOptions::new(1)?).run()?;
    let child = outcome
        .subgraph(NodeId::new(1))
        .ok_or("missing generated graph")?;
    let Some(NodeOutcome::Succeeded(value)) = child.node(NodeId::new(1)) else {
        return Err("missing decoded value".into());
    };
    println!("{}", value.downcast::<usize>()?);
    Ok(())
}
