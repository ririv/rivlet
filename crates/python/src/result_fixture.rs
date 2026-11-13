//! Private whole-graph fixtures exercising the actual Rust/Python value boundary.

use std::marker::PhantomData;
use std::sync::Arc;

use action::{Action, ActionContext, ActionError, ActionHandle};
use graph::{GraphInterface, GraphStructure, Step};
use pyo3::exceptions::PyTypeError;
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::result_values::ValueCodecs;
use crate::rust_graph::RustGraphStructure;

// 这些仅供 FFI 集成测试构造整图，不向 rivlet 根包公开单个 Rust action。
#[pyclass(
    frozen,
    eq,
    eq_int,
    name = "_ResultFixtureKind",
    module = "rivlet._native",
    from_py_object
)]
#[derive(Clone, Copy, PartialEq)]
pub(crate) enum ResultFixtureKind {
    I64,
    U64,
    Bool,
    F64,
    String,
    Bytes,
    Unit,
    Custom,
    Opaque,
}

struct Constant<T>(T);
impl<T: Clone + Send + Sync + 'static> Action for Constant<T> {
    type Inputs = ();
    type Output = T;
    fn execute(&self, _: &ActionContext, (): ()) -> Result<T, ActionError> {
        Ok(self.0.clone())
    }
}

struct Echo<T>(PhantomData<T>);
impl<T: Clone + Send + Sync + 'static> Action for Echo<T> {
    type Inputs = (Option<Arc<T>>,);
    type Output = T;
    fn execute(&self, _: &ActionContext, (value,): Self::Inputs) -> Result<T, ActionError> {
        value
            .map(|value| value.as_ref().clone())
            .ok_or_else(|| ActionError::new("fixture requires a present input"))
    }
}

struct Present;
impl Action for Present {
    type Inputs = (Option<Arc<()>>,);
    type Output = bool;
    fn execute(&self, _: &ActionContext, (value,): Self::Inputs) -> Result<bool, ActionError> {
        Ok(value.is_some())
    }
}

#[derive(Clone)]
struct Plan {
    value: i64,
}

fn export_plan(py: Python<'_>, plan: &Plan) -> PyResult<Py<PyAny>> {
    let dict = PyDict::new(py);
    dict.set_item("value", plan.value)?;
    Ok(dict.into_any().unbind())
}
fn import_plan(value: &Bound<'_, PyAny>) -> PyResult<Plan> {
    let dict = value.cast::<PyDict>()?;
    if dict.len() != 1 {
        return Err(PyTypeError::new_err(
            "Plan requires exactly one value field",
        ));
    }
    let value = dict
        .get_item("value")?
        .ok_or_else(|| PyTypeError::new_err("Plan requires value"))?;
    Ok(Plan {
        value: value.extract()?,
    })
}

fn pair<T: Clone + Send + Sync + 'static>(
    value: T,
    codecs: ValueCodecs,
) -> PyResult<(RustGraphStructure, RustGraphStructure)> {
    let seed = Step::new("seed", ActionHandle::new(Constant(value))).map_err(invalid)?;
    let echo = Step::new("echo", ActionHandle::new(Echo::<T>(PhantomData))).map_err(invalid)?;
    bodies(seed, echo, codecs)
}

fn invalid(error: impl std::fmt::Display) -> PyErr {
    PyTypeError::new_err(error.to_string())
}

fn bodies(
    seed: Step,
    echo: Step,
    codecs: ValueCodecs,
) -> PyResult<(RustGraphStructure, RustGraphStructure)> {
    let output = GraphInterface::default()
        .output("value", &seed)
        .map_err(invalid)?;
    let interface = GraphInterface::default()
        .input("apply", [&echo])
        .map_err(invalid)?
        .output("value", &echo)
        .map_err(invalid)?;
    let codecs = Arc::new(codecs);
    Ok((
        RustGraphStructure::with_codecs(
            GraphStructure::with_interface([seed.into()], [], output).map_err(invalid)?,
            Arc::clone(&codecs),
        )?,
        RustGraphStructure::with_codecs(
            GraphStructure::with_interface([echo.into()], [], interface).map_err(invalid)?,
            codecs,
        )?,
    ))
}

#[pyfunction(name = "_rust_result_fixture")]
pub(crate) fn rust_result_fixture(
    kind: ResultFixtureKind,
) -> PyResult<(RustGraphStructure, RustGraphStructure)> {
    let codecs = ValueCodecs::default();
    match kind {
        ResultFixtureKind::I64 => pair(42_i64, codecs),
        ResultFixtureKind::U64 => pair(u64::MAX, codecs),
        ResultFixtureKind::Bool => pair(true, codecs),
        ResultFixtureKind::F64 => pair(1.25_f64, codecs),
        ResultFixtureKind::String => pair(String::from("native"), codecs),
        ResultFixtureKind::Bytes => pair(vec![0_u8, 255], codecs),
        ResultFixtureKind::Custom => {
            pair(Plan { value: 42 }, codecs.with(export_plan, import_plan)?)
        }
        ResultFixtureKind::Opaque => pair(Plan { value: 42 }, codecs),
        ResultFixtureKind::Unit => bodies(
            Step::new("unit", ActionHandle::new(Constant(()))).map_err(invalid)?,
            Step::new("presence", ActionHandle::new(Present)).map_err(invalid)?,
            codecs,
        ),
    }
}
