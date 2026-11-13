//! Private whole-graph fixtures exercising the actual Rust/Python value boundary.

use std::marker::PhantomData;
use std::sync::Arc;

use action::{NativeAction, NativeActionContext, NativeActionError, NativeActionHandle};
use graph::{GraphInterface, GraphStructure, Leaf};
use pyo3::exceptions::PyTypeError;
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::native_graph::NativeGraphStructure;
use crate::result_values::ValueCodecs;

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
impl<T: Clone + Send + Sync + 'static> NativeAction for Constant<T> {
    type Inputs = ();
    type Output = T;
    fn execute(&self, _: &NativeActionContext, (): ()) -> Result<T, NativeActionError> {
        Ok(self.0.clone())
    }
}

struct Echo<T>(PhantomData<T>);
impl<T: Clone + Send + Sync + 'static> NativeAction for Echo<T> {
    type Inputs = (Option<Arc<T>>,);
    type Output = T;
    fn execute(
        &self,
        _: &NativeActionContext,
        (value,): Self::Inputs,
    ) -> Result<T, NativeActionError> {
        value
            .map(|value| value.as_ref().clone())
            .ok_or_else(|| NativeActionError::new("fixture requires a present input"))
    }
}

struct Present;
impl NativeAction for Present {
    type Inputs = (Option<Arc<()>>,);
    type Output = bool;
    fn execute(
        &self,
        _: &NativeActionContext,
        (value,): Self::Inputs,
    ) -> Result<bool, NativeActionError> {
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
) -> PyResult<(NativeGraphStructure, NativeGraphStructure)> {
    let seed = Leaf::new("seed", NativeActionHandle::new(Constant(value))).map_err(invalid)?;
    let echo =
        Leaf::new("echo", NativeActionHandle::new(Echo::<T>(PhantomData))).map_err(invalid)?;
    bodies(seed, echo, codecs)
}

fn invalid(error: impl std::fmt::Display) -> PyErr {
    PyTypeError::new_err(error.to_string())
}

fn bodies(
    seed: Leaf,
    echo: Leaf,
    codecs: ValueCodecs,
) -> PyResult<(NativeGraphStructure, NativeGraphStructure)> {
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
        NativeGraphStructure::with_codecs(
            GraphStructure::with_interface([seed.into()], [], output).map_err(invalid)?,
            Arc::clone(&codecs),
        )?,
        NativeGraphStructure::with_codecs(
            GraphStructure::with_interface([echo.into()], [], interface).map_err(invalid)?,
            codecs,
        )?,
    ))
}

#[pyfunction(name = "_native_result_fixture")]
pub(crate) fn native_result_fixture(
    kind: ResultFixtureKind,
) -> PyResult<(NativeGraphStructure, NativeGraphStructure)> {
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
            Leaf::new("unit", NativeActionHandle::new(Constant(()))).map_err(invalid)?,
            Leaf::new("presence", NativeActionHandle::new(Present)).map_err(invalid)?,
            codecs,
        ),
    }
}
