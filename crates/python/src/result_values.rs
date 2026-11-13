use std::any::TypeId;
use std::collections::HashMap;
use std::sync::Arc;

use action::{Value, ValueType};
use pyo3::BoundObject;
use pyo3::exceptions::PyTypeError;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{PyBool, PyBytes, PyFloat, PyInt, PyString};

type Export<T> = fn(Python<'_>, &T) -> PyResult<Py<PyAny>>;
type Import<T> = fn(&Bound<'_, PyAny>) -> PyResult<T>;

trait ErasedCodec: Send + Sync {
    fn export(&self, py: Python<'_>, value: &Value) -> PyResult<Py<PyAny>>;
    fn import(&self, value: &Bound<'_, PyAny>) -> PyResult<Value>;
}

struct Codec<T> {
    export: Export<T>,
    import: Import<T>,
}

impl<T: Send + Sync + 'static> ErasedCodec for Codec<T> {
    fn export(&self, py: Python<'_>, value: &Value) -> PyResult<Py<PyAny>> {
        let value = value
            .downcast::<T>()
            .map_err(|error| PyTypeError::new_err(error.to_string()))?;
        (self.export)(py, &value)
    }
    fn import(&self, value: &Bound<'_, PyAny>) -> PyResult<Value> {
        (self.import)(value).map(Value::new)
    }
}

#[derive(Clone, Copy)]
enum Builtin {
    Unit,
    Bool,
    I8,
    I16,
    I32,
    I64,
    I128,
    Isize,
    U8,
    U16,
    U32,
    U64,
    U128,
    Usize,
    F32,
    F64,
    String,
    Bytes,
}

fn builtin(id: TypeId) -> Option<Builtin> {
    [
        (TypeId::of::<()>(), Builtin::Unit),
        (TypeId::of::<bool>(), Builtin::Bool),
        (TypeId::of::<i8>(), Builtin::I8),
        (TypeId::of::<i16>(), Builtin::I16),
        (TypeId::of::<i32>(), Builtin::I32),
        (TypeId::of::<i64>(), Builtin::I64),
        (TypeId::of::<i128>(), Builtin::I128),
        (TypeId::of::<isize>(), Builtin::Isize),
        (TypeId::of::<u8>(), Builtin::U8),
        (TypeId::of::<u16>(), Builtin::U16),
        (TypeId::of::<u32>(), Builtin::U32),
        (TypeId::of::<u64>(), Builtin::U64),
        (TypeId::of::<u128>(), Builtin::U128),
        (TypeId::of::<usize>(), Builtin::Usize),
        (TypeId::of::<f32>(), Builtin::F32),
        (TypeId::of::<f64>(), Builtin::F64),
        (TypeId::of::<String>(), Builtin::String),
        (TypeId::of::<Vec<u8>>(), Builtin::Bytes),
    ]
    .into_iter()
    .find_map(|(candidate, kind)| (candidate == id).then_some(kind))
}

/// Explicit adapter-owned converters; core and action never depend on `PyO3`.
#[derive(Clone, Default)]
pub(crate) struct ValueCodecs {
    custom: HashMap<TypeId, Arc<dyn ErasedCodec>>,
}

impl ValueCodecs {
    pub(crate) fn with<T: Send + Sync + 'static>(
        mut self,
        export: Export<T>,
        import: Import<T>,
    ) -> PyResult<Self> {
        let id = TypeId::of::<T>();
        if builtin(id).is_some() || self.custom.contains_key(&id) {
            return Err(PyTypeError::new_err(
                "native result codec must not be replaced",
            ));
        }
        self.custom.insert(id, Arc::new(Codec { export, import }));
        Ok(self)
    }

    pub(crate) fn combine(&mut self, other: &Self) -> PyResult<()> {
        for (id, codec) in &other.custom {
            if let Some(existing) = self.custom.get(id) {
                if !Arc::ptr_eq(existing, codec) {
                    return Err(PyTypeError::new_err("conflicting native result codecs"));
                }
            } else {
                self.custom.insert(*id, Arc::clone(codec));
            }
        }
        Ok(())
    }

    pub(crate) fn export(&self, py: Python<'_>, value: &Value) -> PyResult<Py<PyAny>> {
        if let Some(codec) = self.custom.get(&value.type_id()) {
            return codec.export(py, value);
        }
        macro_rules! export {
            ($type:ty) => {{
                let value = value
                    .downcast::<$type>()
                    .map_err(|error| PyTypeError::new_err(error.to_string()))?;
                Ok(value
                    .as_ref()
                    .into_pyobject(py)?
                    .into_bound()
                    .into_any()
                    .unbind())
            }};
        }
        match builtin(value.type_id()) {
            Some(Builtin::Unit) => Ok(py.None()),
            Some(Builtin::Bool) => export!(bool),
            Some(Builtin::I8) => export!(i8),
            Some(Builtin::I16) => export!(i16),
            Some(Builtin::I32) => export!(i32),
            Some(Builtin::I64) => export!(i64),
            Some(Builtin::I128) => export!(i128),
            Some(Builtin::Isize) => export!(isize),
            Some(Builtin::U8) => export!(u8),
            Some(Builtin::U16) => export!(u16),
            Some(Builtin::U32) => export!(u32),
            Some(Builtin::U64) => export!(u64),
            Some(Builtin::U128) => export!(u128),
            Some(Builtin::Usize) => export!(usize),
            Some(Builtin::F32) => export!(f32),
            Some(Builtin::F64) => export!(f64),
            Some(Builtin::String) => export!(String),
            Some(Builtin::Bytes) => {
                let bytes = value
                    .downcast::<Vec<u8>>()
                    .map_err(|error| PyTypeError::new_err(error.to_string()))?;
                Ok(PyBytes::new(py, &bytes).into_any().unbind())
            }
            None => Err(PyTypeError::new_err(
                "no Python export codec for this native result type",
            )),
        }
    }

    pub(crate) fn import(&self, expected: ValueType, value: &Bound<'_, PyAny>) -> PyResult<Value> {
        if let Some(codec) = self.custom.get(&expected.id()) {
            return codec.import(value);
        }
        let kind = builtin(expected.id()).ok_or_else(|| {
            PyTypeError::new_err(format!("no Python import codec for {}", expected.name()))
        })?;
        let valid = match kind {
            Builtin::Unit => value.is_none(),
            Builtin::Bool => value.is_instance_of::<PyBool>(),
            Builtin::I8
            | Builtin::I16
            | Builtin::I32
            | Builtin::I64
            | Builtin::I128
            | Builtin::Isize
            | Builtin::U8
            | Builtin::U16
            | Builtin::U32
            | Builtin::U64
            | Builtin::U128
            | Builtin::Usize => {
                value.is_instance_of::<PyInt>() && !value.is_instance_of::<PyBool>()
            }
            Builtin::F32 | Builtin::F64 => value.is_instance_of::<PyFloat>(),
            Builtin::String => value.is_instance_of::<PyString>(),
            Builtin::Bytes => value.is_instance_of::<PyBytes>(),
        };
        if !valid {
            return Err(PyTypeError::new_err(format!(
                "Python result does not match native type {}",
                expected.name()
            )));
        }
        match kind {
            Builtin::Unit => Ok(Value::new(())),
            Builtin::Bool => value.extract::<bool>().map(Value::new),
            Builtin::I8 => value.extract::<i8>().map(Value::new),
            Builtin::I16 => value.extract::<i16>().map(Value::new),
            Builtin::I32 => value.extract::<i32>().map(Value::new),
            Builtin::I64 => value.extract::<i64>().map(Value::new),
            Builtin::I128 => value.extract::<i128>().map(Value::new),
            Builtin::Isize => value.extract::<isize>().map(Value::new),
            Builtin::U8 => value.extract::<u8>().map(Value::new),
            Builtin::U16 => value.extract::<u16>().map(Value::new),
            Builtin::U32 => value.extract::<u32>().map(Value::new),
            Builtin::U64 => value.extract::<u64>().map(Value::new),
            Builtin::U128 => value.extract::<u128>().map(Value::new),
            Builtin::Usize => value.extract::<usize>().map(Value::new),
            Builtin::F32 => value.extract::<f32>().map(Value::new),
            Builtin::F64 => value.extract::<f64>().map(Value::new),
            Builtin::String => value.extract::<String>().map(Value::new),
            Builtin::Bytes => value.extract::<Vec<u8>>().map(Value::new),
        }
    }
}

/// Lazy adapter-owned conversion; pure Rust edges retain the original Arc value.
#[pyclass(
    frozen,
    name = "_RustResult",
    module = "rivlet._native",
    skip_from_py_object
)]
pub(crate) struct RustResult {
    value: Value,
    codecs: Arc<ValueCodecs>,
    converted: PyOnceLock<Py<PyAny>>,
}

impl RustResult {
    pub(crate) fn new(value: Value, codecs: Arc<ValueCodecs>) -> Self {
        Self {
            value,
            codecs,
            converted: PyOnceLock::new(),
        }
    }
}

#[pymethods]
impl RustResult {
    fn to_python(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        self.converted
            .get_or_try_init(py, || self.codecs.export(py, &self.value))
            .map(|value| value.clone_ref(py))
    }
}
