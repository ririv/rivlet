//! Composable Rust graph declarations lowering into the shared execution core.
//!
//! Leaf/Graph own metadata; `GraphStructure` is reusable. Node is their common
//! reference interface and `NodeKind` is the closed concrete union. Result edges
//! bind typed action inputs; Rust predicate execution remains outside this API.
//!
//! ```
//! use std::sync::Arc;
//! use action::{NativeAction, NativeActionContext, NativeActionError, NativeActionHandle};
//! use graph::{Edge, Graph, GraphInterface, GraphStructure, Leaf};
//!
//! struct Work;
//! impl NativeAction for Work {
//!     type Inputs = ();
//!     type Output = ();
//!     fn execute(&self, _: &NativeActionContext, (): ()) -> Result<(), NativeActionError> {
//!         Ok(())
//!     }
//! }
//! # fn main() -> Result<(), Box<dyn std::error::Error>> {
//! let task = Leaf::new("task", NativeActionHandle::new(Work))?;
//! let body = Arc::new(GraphStructure::with_interface(
//!     [task.clone().into()], [],
//!     GraphInterface::default().input("apply", [&task])?.output("ready", &task)?,
//! )?);
//! let a = Graph::new("a", Arc::clone(&body))?;
//! let b = Graph::new("b", body)?;
//! let connection = Edge::ports(a.interface().output("ready")?, b.interface().input("apply")?);
//! let root = Graph::new("root", Arc::new(GraphStructure::new(
//!     [a.into(), b.into()], [connection],
//! )?))?;
//! let compilation = root.compile()?;
//! assert_eq!(compilation.graph().nodes().len(), 5);
//! # Ok(())
//! # }
//! ```

mod edge;
mod error;
mod interface;
mod node;
mod projection;
mod structure;

pub use edge::{Edge, ResultSources, ResultTarget};
pub use error::GraphError;
pub use interface::{
    GraphInterface, GraphInterfaceView, InputEndpoint, InputPort, OutputEndpoint, OutputPort,
};
pub use node::{Graph, Leaf, Node, NodeKind};
pub use projection::{Compilation, ProjectedKind, ProjectedNode, ProjectedScope, Projection};
pub use structure::GraphStructure;
