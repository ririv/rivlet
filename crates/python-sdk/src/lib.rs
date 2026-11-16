//! Export concrete Rust nodes as ordinary Python Step or Graph objects.
//!
//! Experimental: this SDK and Python/Rust mixed execution are one feature family.
//! The API/ABI may change or be removed; no experimental namespace is required.

use graph::NodeKind;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyCapsule;
use rivlet_bridge::{ABI_NAME, ExportedGraph};

#[allow(unsafe_code)]
mod capsule;
pub use capsule::register_importer;

/// Exports a concrete node through the native bridge, never through Python actions.
///
/// Experimental; no stable API/ABI or continued-availability guarantee.
///
/// # Errors
/// Returns graph/type validation errors, an incompatible host ABI, or Python import errors.
pub fn export_node(py: Python<'_>, node: impl Into<NodeKind>) -> PyResult<Py<PyAny>> {
    let exported = ExportedGraph::from_node(node.into())
        .map_err(|error| PyValueError::new_err(error.to_string()))?;
    let capsule = PyCapsule::new_with_value(py, exported, ABI_NAME)?;
    py.import("rivlet._native")?
        .getattr("_import_node")?
        .call1((capsule,))
        .map(Bound::unbind)
}
