//! Thin JSON-value conversion for the shared inspection model; no text roundtrip.
use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyInt, PyList, PyString};
use serde_json::Value;

fn read(value: &Bound<'_, PyAny>) -> PyResult<Value> {
    if value.is_none() {
        return Ok(Value::Null);
    }
    if value.is_instance_of::<PyBool>() {
        return value.extract::<bool>().map(Value::Bool);
    }
    if value.is_instance_of::<PyString>() {
        return value.extract::<String>().map(Value::String);
    }
    if value.is_instance_of::<PyInt>() {
        return value.extract::<u64>().map(|v| Value::Number(v.into()));
    }
    if let Ok(list) = value.cast::<PyList>() {
        return list
            .iter()
            .map(|item| read(&item))
            .collect::<PyResult<Vec<_>>>()
            .map(Value::Array);
    }
    if let Ok(dict) = value.cast::<PyDict>() {
        return dict
            .iter()
            .map(|(key, value)| Ok((key.extract::<String>()?, read(&value)?)))
            .collect::<PyResult<serde_json::Map<_, _>>>()
            .map(Value::Object);
    }
    Err(PyTypeError::new_err(
        "graph inspection accepts only JSON document values",
    ))
}
fn write(py: Python<'_>, value: Value) -> PyResult<Py<PyAny>> {
    match value {
        Value::Null => Ok(py.None()),
        Value::Bool(v) => Ok(v.into_pyobject(py)?.to_owned().into_any().unbind()),
        Value::String(v) => Ok(v.into_pyobject(py)?.into_any().unbind()),
        Value::Number(v) => Ok(v
            .as_u64()
            .ok_or_else(|| PyValueError::new_err("inspection integer is outside u64"))?
            .into_pyobject(py)?
            .into_any()
            .unbind()),
        Value::Array(values) => {
            let list = PyList::empty(py);
            for value in values {
                list.append(write(py, value)?)?;
            }
            Ok(list.into_any().unbind())
        }
        Value::Object(values) => {
            let dict = PyDict::new(py);
            for (key, value) in values {
                dict.set_item(key, write(py, value)?)?;
            }
            Ok(dict.into_any().unbind())
        }
    }
}
#[pyfunction]
pub(crate) fn validate_graph_document(
    py: Python<'_>,
    value: &Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    let value = read(value)?;
    let normalized = py
        .detach(move || {
            let document = inspection::from_value(value)?;
            inspection::to_value(&document)
        })
        .map_err(|error| PyValueError::new_err(error.to_string()))?;
    write(py, normalized)
}
#[pyfunction]
pub(crate) fn render_graph_document(
    py: Python<'_>,
    value: &Bound<'_, PyAny>,
    format: &str,
    root_label: String,
    condition_labels: std::collections::BTreeMap<String, String>,
) -> PyResult<String> {
    let document = inspection::from_value(read(value)?)
        .map_err(|error| PyValueError::new_err(error.to_string()))?;
    let options = inspection::RenderOptions {
        root_label: Some(root_label),
        condition_labels,
    };
    py.detach(|| match format {
        "tree" => inspection::render_tree(&document, &options),
        "mermaid" => inspection::render_mermaid(&document, &options),
        _ => Err(inspection::DocumentError(
            "unknown graph render format".into(),
        )),
    })
    .map_err(|error| PyValueError::new_err(error.to_string()))
}
