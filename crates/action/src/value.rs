use std::any::{Any, TypeId, type_name};
use std::fmt::{self, Debug, Formatter};
use std::sync::Arc;

use crate::ActionError;

/// An exact Rust type identity, independent of language conversion policies.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ValueType {
    id: ValueTypeId,
    name: &'static str,
}

/// Runtime identity of a Rust type or an opaque type owned by another library.
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub enum ValueTypeId {
    Rust(TypeId),
    /// Experimental mixed-execution identity owned by another library.
    External {
        provider: usize,
        key: u64,
    },
}

impl ValueType {
    #[must_use]
    pub fn of<T: Send + Sync + 'static>() -> Self {
        Self {
            id: ValueTypeId::Rust(TypeId::of::<T>()),
            name: type_name::<T>(),
        }
    }
    #[must_use]
    pub const fn name(self) -> &'static str {
        self.name
    }
    #[must_use]
    pub const fn id(self) -> ValueTypeId {
        self.id
    }
    #[must_use]
    pub fn is<T: Send + Sync + 'static>(self) -> bool {
        self.id == ValueTypeId::Rust(TypeId::of::<T>())
    }

    #[must_use]
    pub const fn external(provider: usize, key: u64) -> Self {
        Self {
            id: ValueTypeId::External { provider, key },
            name: "external value",
        }
    }
}

/// Experimental foreign-value ownership for mixed execution and its extension SDK.
pub trait ExternalValue: Send + Sync + 'static {
    fn value_type(&self) -> ValueType;
    /// Converts only when a consumer explicitly requests a native Rust representation.
    ///
    /// # Errors
    /// Returns the transport/conversion failure, including a missing custom converter.
    fn materialize(&self) -> Result<Value, ActionError>;
    fn as_any(&self) -> &dyn Any;
}

/// Shared immutable ownership of a typed Rust result, never serialized implicitly.
#[derive(Clone)]
pub struct Value(StoredValue);

#[derive(Clone)]
enum StoredValue {
    Unit,
    Shared(Arc<dyn Any + Send + Sync>),
    External(Arc<dyn ExternalValue>),
}

impl Value {
    #[must_use]
    pub fn new<T: Send + Sync + 'static>(value: T) -> Self {
        // 常见 ProcessAction 的 () 不分配结果载荷，保留无值动作的轻量路径。
        Self(if TypeId::of::<T>() == TypeId::of::<()>() {
            StoredValue::Unit
        } else {
            StoredValue::Shared(Arc::new(value))
        })
    }

    /// Returns shared ownership without copying the underlying result.
    ///
    /// # Errors
    /// Returns an error when the requested concrete type differs.
    pub fn downcast<T: Send + Sync + 'static>(&self) -> Result<Arc<T>, ActionError> {
        // Any 只承担内部类型擦除；公开 action 使用确定的关联类型，不能任意强转。
        let value: Arc<dyn Any + Send + Sync> = match &self.0 {
            StoredValue::Unit => Arc::new(()),
            StoredValue::Shared(value) => Arc::clone(value),
            StoredValue::External(value) => return value.materialize()?.downcast::<T>(),
        };
        value.downcast::<T>().map_err(|_| {
            ActionError::new(format!(
                "native result type mismatch: expected {}",
                type_name::<T>()
            ))
        })
    }
    #[must_use]
    pub fn type_id(&self) -> ValueTypeId {
        match &self.0 {
            StoredValue::Unit => ValueTypeId::Rust(TypeId::of::<()>()),
            StoredValue::Shared(value) => ValueTypeId::Rust(value.as_ref().type_id()),
            StoredValue::External(value) => value.value_type().id(),
        }
    }

    #[must_use]
    pub fn external(value: impl ExternalValue) -> Self {
        Self(StoredValue::External(Arc::new(value)))
    }

    #[must_use]
    pub fn external_ref<T: ExternalValue>(&self) -> Option<&T> {
        match &self.0 {
            StoredValue::External(value) => value.as_any().downcast_ref(),
            StoredValue::Unit | StoredValue::Shared(_) => None,
        }
    }
}

impl Debug for Value {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter.write_str("Value(..)")
    }
}

/// A typed ordered action input group; absent slots represent skipped sources.
pub trait ActionInputs: Send + 'static {
    fn types() -> Vec<ValueType>;
    /// Decodes one complete group after the caller validates its length.
    ///
    /// # Errors
    /// Returns an error for a concrete value type mismatch.
    fn decode(values: &[Option<Value>]) -> Result<Self, ActionError>
    where
        Self: Sized;
}

impl ActionInputs for () {
    fn types() -> Vec<ValueType> {
        Vec::new()
    }
    fn decode(_: &[Option<Value>]) -> Result<Self, ActionError> {
        Ok(())
    }
}

macro_rules! input_tuple {
    ($($name:ident:$index:tt),+) => {
        impl<$($name: Send + Sync + 'static),+> ActionInputs for ($(Option<Arc<$name>>,)+) {
            fn types() -> Vec<ValueType> { vec![$(ValueType::of::<$name>()),+] }
            fn decode(values: &[Option<Value>]) -> Result<Self, ActionError> {
                Ok(($(values[$index].as_ref().map(Value::downcast::<$name>).transpose()?,)+))
            }
        }
    };
}

input_tuple!(A:0);
input_tuple!(A:0,B:1);
input_tuple!(A:0,B:1,C:2);
input_tuple!(A:0,B:1,C:2,D:3);
input_tuple!(A:0,B:1,C:2,D:3,E:4);
input_tuple!(A:0,B:1,C:2,D:3,E:4,F:5);
input_tuple!(A:0,B:1,C:2,D:3,E:4,F:5,G:6);
input_tuple!(A:0,B:1,C:2,D:3,E:4,F:5,G:6,H:7);
input_tuple!(A:0,B:1,C:2,D:3,E:4,F:5,G:6,H:7,I:8);
input_tuple!(A:0,B:1,C:2,D:3,E:4,F:5,G:6,H:7,I:8,J:9);
input_tuple!(A:0,B:1,C:2,D:3,E:4,F:5,G:6,H:7,I:8,J:9,K:10);
input_tuple!(A:0,B:1,C:2,D:3,E:4,F:5,G:6,H:7,I:8,J:9,K:10,L:11);
