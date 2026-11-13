use std::any::{Any, TypeId, type_name};
use std::fmt::{self, Debug, Formatter};
use std::sync::Arc;

use crate::ActionError;

/// An exact Rust type identity, independent of language conversion policies.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ValueType {
    id: TypeId,
    name: &'static str,
}

impl ValueType {
    #[must_use]
    pub fn of<T: Send + Sync + 'static>() -> Self {
        Self {
            id: TypeId::of::<T>(),
            name: type_name::<T>(),
        }
    }
    #[must_use]
    pub const fn name(self) -> &'static str {
        self.name
    }
    #[must_use]
    pub const fn id(self) -> TypeId {
        self.id
    }
    #[must_use]
    pub fn is<T: Send + Sync + 'static>(self) -> bool {
        self.id == TypeId::of::<T>()
    }
}

/// Shared immutable ownership of a typed Rust result, never serialized implicitly.
#[derive(Clone)]
pub struct Value(StoredValue);

#[derive(Clone)]
enum StoredValue {
    Unit,
    Shared(Arc<dyn Any + Send + Sync>),
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
        };
        value.downcast::<T>().map_err(|_| {
            ActionError::new(format!(
                "native result type mismatch: expected {}",
                type_name::<T>()
            ))
        })
    }
    #[must_use]
    pub fn type_id(&self) -> TypeId {
        match &self.0 {
            StoredValue::Unit => TypeId::of::<()>(),
            StoredValue::Shared(value) => value.as_ref().type_id(),
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
