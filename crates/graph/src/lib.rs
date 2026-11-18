//! Composable Rust graph declarations lowering into the shared execution core.
//!
//! Step/Graph own metadata; `Graph` is reusable. Node is their common
//! reference interface and `NodeKind` is the closed concrete union. Result edges
//! bind typed action inputs; typed runtime predicates remain Rust-owned.
//!
//! ```
//! use std::sync::Arc;
//! use rivlet_action::{Action, ActionContext, ActionError, ActionHandle};
//! use rivlet_graph::{Edge, Graph, GraphInterface, Step};
//!
//! struct Work;
//! impl Action for Work {
//!     type Inputs = ();
//!     type Output = ();
//!     fn execute(&self, _: &ActionContext, (): ()) -> Result<(), ActionError> {
//!         Ok(())
//!     }
//! }
//! # fn main() -> Result<(), Box<dyn std::error::Error>> {
//! let task = Step::new("task", ActionHandle::new(Work))?;
//! let body = Arc::new(Graph::with_interface("scope",
//!     [task.clone().into()], [],
//!     GraphInterface::default().input("apply", [&task])?.output("ready", &task)?,
//! )?);
//! let a = Graph::with_interface("a", body.nodes().iter().cloned(), body.edges().iter().cloned(), body.interface().declaration().clone())?;
//! let b = Graph::with_interface("b", body.nodes().iter().cloned(), body.edges().iter().cloned(), body.interface().declaration().clone())?;
//! let connection = Edge::ports(a.interface().output("ready")?, b.interface().input("apply")?);
//! let root = Graph::new("root", [a.into(), b.into()], [connection])?;
//! let compilation = root.compile()?;
//! assert_eq!(compilation.graph().nodes().len(), 5);
//! # Ok(())
//! # }
//! ```

mod condition;
mod edge;
mod error;
mod interface;
mod node;
mod projection;
mod validation;

pub use condition::Condition;
pub use edge::{Edge, ResultSources, ResultTarget};
pub use error::GraphError;
pub use interface::{
    GraphInterface, GraphInterfaceView, InputEndpoint, InputPort, OutputEndpoint, OutputPort,
    PortName,
};
pub use node::{Graph, Node, NodeKind, Step};
pub use projection::{
    Compilation, ProjectedCondition, ProjectedKind, ProjectedNode, ProjectedScope, Projection,
};
