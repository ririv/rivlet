//! Explicit shared state stays with the host, not the compilation or result store.
use action::{Action, ActionContext, ActionError, ActionHandle};
use execution::{Execution, ExecutionOptions, ExecutionStatus};
use graph::{Graph, Step};
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering},
};

#[derive(Default)]
struct Progress(AtomicUsize);
struct Record;
impl Action for Record {
    type Inputs = ();
    type Output = ();
    fn execute(&self, ctx: &ActionContext, (): ()) -> Result<(), ActionError> {
        ctx.shared::<Progress>()?.0.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let first = Step::new("first", ActionHandle::new(Record))?;
    let second = Step::new("second", ActionHandle::new(Record))?;
    let workflow = Graph::new("workflow", [first.into(), second.into()], [])?;
    let progress = Arc::new(Progress::default());
    let options = ExecutionOptions::new(2)?.shared(Arc::clone(&progress));
    let outcome = Execution::new(workflow.compile()?, options).run()?;
    assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
    println!("{}", progress.0.load(Ordering::SeqCst));
    Ok(())
}
