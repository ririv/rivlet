use std::any::Any;
use std::ffi::c_void;
use std::mem::size_of;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::ptr;
use std::sync::atomic::AtomicU8;
use std::sync::{Arc, OnceLock};

use super::{
    ABI_VERSION, Bytes, ImportedGraph, Input, Outcome, OwnedBytes, Payload, Prefix, RawType,
    RawValue, ValueFunctions,
};
use crate::{Type, types};
use action::{ActionError, ExternalValue, Value, ValueType};

struct ExportValue {
    value: Value,
    kind: Type,
    bytes: OnceLock<Result<Vec<u8>, ActionError>>,
}
static PROVIDER: AtomicU8 = AtomicU8::new(0);

pub(super) unsafe extern "C" fn provider() -> usize {
    ptr::addr_of!(PROVIDER) as usize
}
unsafe extern "C" fn release_bytes(owner: *const c_void) {
    // 仅由相同库创建的 Box<Vec<u8>> 传入；主库从不接管该 Vec 的分配器。
    drop(unsafe { Box::from_raw(owner.cast_mut().cast::<Vec<u8>>()) });
}

pub(super) fn error(message: &(impl ToString + ?Sized)) -> Outcome {
    let owner = Box::new(message.to_string().into_bytes());
    let bytes = Bytes::of(&owner);
    Outcome {
        tag: 1,
        payload: Payload {
            error: OwnedBytes {
                bytes,
                owner: Box::into_raw(owner).cast(),
                release: release_bytes,
            },
        },
    }
}

pub(super) fn export_value(value: Value) -> Result<RawValue, ActionError> {
    let kind = types::describe_value(&value)?;
    Ok(RawValue {
        context: Arc::into_raw(Arc::new(ExportValue {
            value,
            kind,
            bytes: OnceLock::new(),
        }))
        .cast(),
        functions: &raw const VALUE_FUNCTIONS,
    })
}

fn wire_type(kind: &Type) -> RawType {
    let tag = match kind {
        Type::Unit => 0,
        Type::Bool => 1,
        Type::I8 => 2,
        Type::I16 => 3,
        Type::I32 => 4,
        Type::I64 => 5,
        Type::I128 => 6,
        Type::Isize => 7,
        Type::U8 => 8,
        Type::U16 => 9,
        Type::U32 => 10,
        Type::U64 => 11,
        Type::U128 => 12,
        Type::Usize => 13,
        Type::F32 => 14,
        Type::F64 => 15,
        Type::String => 16,
        Type::Bytes => 17,
        Type::Custom { key, .. } => return RawType { tag: 18, key: *key },
    };
    RawType { tag, key: 0 }
}

pub(super) fn parse_type(kind: RawType) -> Result<Type, ActionError> {
    if kind.tag != 18 && kind.key != 0 {
        return Err(ActionError::new(
            "builtin native type contains a custom key",
        ));
    }
    Ok(match kind.tag {
        0 => Type::Unit,
        1 => Type::Bool,
        2 => Type::I8,
        3 => Type::I16,
        4 => Type::I32,
        5 => Type::I64,
        6 => Type::I128,
        7 => Type::Isize,
        8 => Type::U8,
        9 => Type::U16,
        10 => Type::U32,
        11 => Type::U64,
        12 => Type::U128,
        13 => Type::Usize,
        14 => Type::F32,
        15 => Type::F64,
        16 => Type::String,
        17 => Type::Bytes,
        18 => Type::Custom {
            key: kind.key,
            name: "opaque".to_owned(),
        },
        _ => return Err(ActionError::new("unknown native ABI value type")),
    })
}

unsafe extern "C" fn value_kind(context: *const c_void) -> RawType {
    // context 的创建、类型解释和释放全部留在当前库。
    wire_type(&unsafe { &*context.cast::<ExportValue>() }.kind)
}
unsafe extern "C" fn value_release(context: *const c_void) {
    drop(unsafe { Arc::from_raw(context.cast::<ExportValue>()) });
}
unsafe extern "C" fn value_read(context: *const c_void) -> Outcome {
    let operation = || {
        let owner = unsafe { &*context.cast::<ExportValue>() };
        match owner
            .bytes
            .get_or_init(|| types::encode(&owner.value, &owner.kind))
        {
            Ok(bytes) => Outcome {
                tag: 3,
                payload: Payload {
                    bytes: Bytes::of(bytes),
                },
            },
            Err(message) => error(message),
        }
    };
    catch_unwind(AssertUnwindSafe(operation))
        .unwrap_or_else(|_| error("native value conversion panicked"))
}
static VALUE_FUNCTIONS: ValueFunctions = ValueFunctions {
    version: ABI_VERSION,
    size: size_of::<ValueFunctions>(),
    provider,
    kind: value_kind,
    read: value_read,
    release: value_release,
};

pub(super) unsafe fn functions<'a>(raw: RawValue) -> Result<&'a ValueFunctions, ActionError> {
    if raw.context.is_null() || raw.functions.is_null() {
        return Err(ActionError::new("native value handle is null"));
    }
    let prefix = unsafe { &*raw.functions.cast::<Prefix>() };
    if prefix.version != ABI_VERSION || prefix.size != size_of::<ValueFunctions>() {
        return Err(ActionError::new("incompatible native value ABI"));
    }
    Ok(unsafe { &*raw.functions })
}

// 此入口消费一次所有权并释放原库分配，不能改成可重复借用同一结果。
#[allow(clippy::needless_pass_by_value)]
pub(super) unsafe fn take_error(outcome: Outcome) -> ActionError {
    let owned = unsafe { outcome.payload.error };
    let message = std::str::from_utf8(unsafe { owned.bytes.slice() }).map(str::to_owned);
    unsafe {
        (owned.release)(owned.owner);
    }
    match message {
        Ok(message) => ActionError::new(message),
        Err(error) => ActionError::new(format!("native error is not UTF-8: {error}")),
    }
}

unsafe fn decode_input(raw: RawValue) -> Result<Value, ActionError> {
    let table = unsafe { functions(raw)? };
    if unsafe { (table.provider)() } == unsafe { provider() } {
        // 只有 provider token 指向当前库，才解释当前库自己的 ExportValue。
        return Ok(unsafe { &*raw.context.cast::<ExportValue>() }.value.clone());
    }
    let kind = parse_type(unsafe { (table.kind)(raw.context) })?;
    let outcome = unsafe { (table.read)(raw.context) };
    match outcome.tag {
        1 => Err(unsafe { take_error(outcome) }),
        3 => types::decode(unsafe { outcome.payload.bytes.slice() }, &kind),
        _ => Err(ActionError::new(
            "native value reader returned an invalid outcome",
        )),
    }
}

pub(super) unsafe fn inputs(
    pointer: *const Input,
    count: usize,
) -> Result<Vec<Option<Value>>, ActionError> {
    let inputs = if count == 0 {
        &[]
    } else {
        if pointer.is_null() {
            return Err(ActionError::new("native input group is null"));
        }
        unsafe { std::slice::from_raw_parts(pointer, count) }
    };
    inputs
        .iter()
        .map(|input| match input.present {
            0 => Ok(None),
            1 => unsafe { decode_input(input.value) }.map(Some),
            _ => Err(ActionError::new("invalid native optional input tag")),
        })
        .collect()
}

pub(super) struct OwnedValue {
    pub(super) raw: RawValue,
    pub(super) graph: Arc<ImportedGraph>,
    pub(super) kind: Type,
}
// 与 ImportedGraph 相同的 lease 和线程安全约定；不在当前库释放或 downcast 外部对象。
unsafe impl Send for OwnedValue {}
unsafe impl Sync for OwnedValue {}
impl Drop for OwnedValue {
    fn drop(&mut self) {
        unsafe {
            ((*self.raw.functions).release)(self.raw.context);
        }
    }
}
impl ExternalValue for OwnedValue {
    fn value_type(&self) -> ValueType {
        crate::value_type(&self.kind, self.graph.provider())
    }
    fn materialize(&self) -> Result<Value, ActionError> {
        let outcome = unsafe { ((*self.raw.functions).read)(self.raw.context) };
        match outcome.tag {
            1 => Err(unsafe { take_error(outcome) }),
            3 => types::decode(unsafe { outcome.payload.bytes.slice() }, &self.kind),
            _ => Err(ActionError::new(
                "native value reader returned an invalid outcome",
            )),
        }
    }
    fn as_any(&self) -> &dyn Any {
        self
    }
}
