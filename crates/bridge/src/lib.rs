//! Experimental mixed-execution transport; Rust objects never cross the ABI boundary.
//!
//! Part of the experimental Python/Rust mixed-execution feature, including its SDK.
//! Neither this ABI nor its continued availability is a stable compatibility promise.

#[allow(unsafe_code)]
mod abi;
mod snapshot;
mod types;

pub use abi::{ABI_NAME, ABI_VERSION, ExportedGraph, ImportedGraph};
pub use snapshot::{
    ConditionInfo, EdgeInfo, NodeInfo, NodeKind, PrerequisiteInfo, ScopeInfo, Site, Snapshot,
};
pub use types::{Type, value_type};
