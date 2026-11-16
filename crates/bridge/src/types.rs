use std::any::TypeId;
use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};

use action::{ActionError, Value, ValueType, ValueTypeId};
use serde::{Deserialize, Serialize};

/// Wire identities are builtins or keys local to the exporting library, never `TypeId` bytes.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub enum Type {
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
    Custom { key: u64, name: String },
}

static TYPES: OnceLock<Mutex<HashMap<TypeId, u64>>> = OnceLock::new();

macro_rules! builtin_types {
    ($callback:ident $(, $argument:expr)*) => {
        $callback!($($argument,)*
            Unit => (), Bool => bool, I8 => i8, I16 => i16, I32 => i32,
            I64 => i64, I128 => i128, Isize => isize, U8 => u8, U16 => u16,
            U32 => u32, U64 => u64, U128 => u128, Usize => usize,
            F32 => f32, F64 => f64, String => String, Bytes => Vec<u8>
        )
    };
}

#[must_use]
pub fn value_type(kind: &Type, provider: usize) -> ValueType {
    macro_rules! resolve {
        ($($variant:ident => $rust:ty),*) => { match kind {
            $(Type::$variant => ValueType::of::<$rust>(),)*
            Type::Custom { key, .. } => ValueType::external(provider, *key),
        }};
    }
    builtin_types!(resolve)
}

pub(crate) fn describe(kind: ValueType) -> Result<Type, ActionError> {
    macro_rules! identify {
        ($($variant:ident => $rust:ty),*) => { $(if kind.is::<$rust>() { return Ok(Type::$variant); })* };
    }
    builtin_types!(identify);
    let ValueTypeId::Rust(id) = kind.id() else {
        return Err(ActionError::new(
            "re-exporting foreign type ownership is not supported",
        ));
    };
    let mut types = TYPES
        .get_or_init(Mutex::default)
        .lock()
        .map_err(|_| ActionError::new("native type registry is poisoned"))?;
    let next = u64::try_from(types.len()).map_err(|error| ActionError::new(error.to_string()))?;
    let key = *types.entry(id).or_insert(next);
    Ok(Type::Custom {
        key,
        name: kind.name().to_owned(),
    })
}

pub(crate) fn describe_value(value: &Value) -> Result<Type, ActionError> {
    macro_rules! identify {
        ($($variant:ident => $rust:ty),*) => { $(if value.type_id() == ValueType::of::<$rust>().id() { return Ok(Type::$variant); })* };
    }
    builtin_types!(identify);
    let ValueTypeId::Rust(id) = value.type_id() else {
        return Err(ActionError::new(
            "foreign values retain their original transport owner",
        ));
    };
    let types = TYPES
        .get_or_init(Mutex::default)
        .lock()
        .map_err(|_| ActionError::new("native type registry is poisoned"))?;
    let key = types
        .get(&id)
        .copied()
        .ok_or_else(|| ActionError::new("value type was not declared by its exporting library"))?;
    Ok(Type::Custom {
        key,
        name: "opaque".to_owned(),
    })
}

pub(crate) fn encode(value: &Value, kind: &Type) -> Result<Vec<u8>, ActionError> {
    macro_rules! numeric {
        ($($variant:ident => $rust:ty),*) => { match kind {
            $(Type::$variant => Ok(value.downcast::<$rust>()?.to_le_bytes().to_vec()),)*
            Type::Unit => Ok(Vec::new()),
            Type::Bool => Ok(vec![u8::from(*value.downcast::<bool>()?)]),
            Type::String => Ok(value.downcast::<String>()?.as_bytes().to_vec()),
            Type::Bytes => Ok(value.downcast::<Vec<u8>>()?.as_ref().clone()),
            Type::Custom { .. } => Err(ActionError::new("custom native value has no cross-library converter")),
        }};
    }
    numeric!(I8=>i8,I16=>i16,I32=>i32,I64=>i64,I128=>i128,Isize=>isize,U8=>u8,U16=>u16,U32=>u32,U64=>u64,U128=>u128,Usize=>usize,F32=>f32,F64=>f64)
}

pub(crate) fn decode(bytes: &[u8], kind: &Type) -> Result<Value, ActionError> {
    macro_rules! numeric {
        ($($variant:ident => $rust:ty),*) => { match kind {
            $(Type::$variant => {
                let array = bytes.try_into().map_err(|_| ActionError::new("native numeric value has an invalid wire length"))?;
                Ok(Value::new(<$rust>::from_le_bytes(array)))
            },)*
            Type::Unit if bytes.is_empty() => Ok(Value::new(())),
            Type::Bool if bytes == [0] || bytes == [1] => Ok(Value::new(bytes[0] == 1)),
            Type::String => String::from_utf8(bytes.to_vec()).map(Value::new).map_err(|error| ActionError::new(error.to_string())),
            Type::Bytes => Ok(Value::new(bytes.to_vec())),
            Type::Custom { .. } => Err(ActionError::new("custom native value has no cross-library converter")),
            Type::Unit | Type::Bool => Err(ActionError::new("native value has an invalid wire representation")),
        }};
    }
    numeric!(I8=>i8,I16=>i16,I32=>i32,I64=>i64,I128=>i128,Isize=>isize,U8=>u8,U16=>u16,U32=>u32,U64=>u64,U128=>u128,Usize=>usize,F32=>f32,F64=>f64)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn round_trip<T>(value: T)
    where
        T: Send + Sync + PartialEq + std::fmt::Debug + 'static,
    {
        let kind = describe(ValueType::of::<T>()).unwrap();
        let original = Value::new(value);
        let bytes = encode(&original, &kind).unwrap();
        assert_eq!(
            *decode(&bytes, &kind).unwrap().downcast::<T>().unwrap(),
            *original.downcast::<T>().unwrap()
        );
    }

    #[test]
    fn builtins_preserve_values_and_numeric_boundaries() {
        round_trip(());
        round_trip(true);
        round_trip(false);
        round_trip(i8::MIN);
        round_trip(i16::MIN);
        round_trip(i32::MIN);
        round_trip(i64::MIN);
        round_trip(i128::MIN);
        round_trip(isize::MIN);
        round_trip(u8::MAX);
        round_trip(u16::MAX);
        round_trip(u32::MAX);
        round_trip(u64::MAX);
        round_trip(u128::MAX);
        round_trip(usize::MAX);
        round_trip(f32::NEG_INFINITY);
        round_trip(-0.0_f64);
        round_trip("跨语言\0字符串".to_owned());
        round_trip(vec![0_u8, 1, 255]);
        let nan = f64::from_bits(0x7ff8_0000_0000_0001);
        let bytes = encode(&Value::new(nan), &Type::F64).unwrap();
        assert_eq!(
            decode(&bytes, &Type::F64)
                .unwrap()
                .downcast::<f64>()
                .unwrap()
                .to_bits(),
            nan.to_bits()
        );
    }

    #[test]
    fn malformed_wire_representations_are_rejected() {
        for (bytes, kind) in [
            (&[1][..], Type::Unit),
            (&[2][..], Type::Bool),
            (&[][..], Type::Bool),
            (&[0; 7][..], Type::I64),
            (&[255][..], Type::String),
        ] {
            assert!(decode(bytes, &kind).is_err());
        }
    }
}
