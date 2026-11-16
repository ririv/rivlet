//! Named capsule validation is the only Python-specific raw-pointer operation.

use std::sync::Arc;

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyCapsule, PyCapsuleMethods};
use rivlet_bridge::{ABI_NAME, ImportedGraph};

/// Imports an SDK-created capsule while retaining its original destructor and library ownership.
///
/// # Errors
/// Rejects a different capsule name, version, descriptor, or callback contract.
unsafe fn import_node(capsule: &Bound<'_, PyCapsule>) -> PyResult<Arc<ImportedGraph>> {
    let pointer = capsule.pointer_checked(Some(ABI_NAME))?;
    let owner = Arc::new(capsule.clone().unbind());
    // 命名 capsule 只由遵守该 C ABI 的原生扩展提供；从不跨库解引用 Rust 对象。
    unsafe { ImportedGraph::from_raw(pointer.as_ptr(), owner) }
        .map_err(|error| PyValueError::new_err(error.to_string()))
}

type Importer = for<'py> fn(Python<'py>, Arc<ImportedGraph>) -> PyResult<Py<PyAny>>;

#[pyclass(frozen, module = "rivlet._native")]
struct NodeImporter {
    function: Importer,
}

#[pymethods]
impl NodeImporter {
    fn __call__(&self, capsule: &Bound<'_, PyCapsule>) -> PyResult<Py<PyAny>> {
        // 这是原生扩展间的 C ABI 入口；名称和布局版本由 SDK 校验，生产方须遵守 ABI。
        let graph = unsafe { import_node(capsule) }?;
        (self.function)(capsule.py(), graph)
    }
}

/// Installs the host's private native-import entry without exposing capsule plumbing to users.
///
/// # Errors
/// Returns Python allocation or module registration errors.
pub fn register_importer(module: &Bound<'_, PyModule>, function: Importer) -> PyResult<()> {
    module.add(
        "_import_node",
        Py::new(module.py(), NodeImporter { function })?,
    )
}
