//! The only raw-pointer boundary; all ownership is released by its creating library.

use std::ffi::{CStr, c_void};

mod export;
mod import;
mod values;

pub use export::ExportedGraph;
pub use import::ImportedGraph;
pub const ABI_VERSION: u32 = 2;
pub const ABI_NAME: &CStr = c"rivlet.node.v2";

#[repr(C)]
#[derive(Clone, Copy)]
struct Bytes {
    data: *const u8,
    len: usize,
}
impl Bytes {
    fn of(value: &[u8]) -> Self {
        Self {
            data: value.as_ptr(),
            len: value.len(),
        }
    }
    unsafe fn slice<'a>(self) -> &'a [u8] {
        if self.len == 0 {
            &[]
        } else {
            // 调用方保证持有创建该缓冲区的 graph/value lease，且长度来自相同 ABI。
            unsafe { std::slice::from_raw_parts(self.data, self.len) }
        }
    }
}

#[repr(C)]
#[derive(Clone, Copy)]
struct OwnedBytes {
    bytes: Bytes,
    owner: *const c_void,
    release: unsafe extern "C" fn(*const c_void),
}

#[repr(C)]
#[derive(Clone, Copy)]
struct RawValue {
    context: *const c_void,
    functions: *const ValueFunctions,
}
#[repr(C)]
struct ValueFunctions {
    version: u32,
    size: usize,
    provider: unsafe extern "C" fn() -> usize,
    kind: unsafe extern "C" fn(*const c_void) -> RawType,
    read: unsafe extern "C" fn(*const c_void) -> Outcome,
    release: unsafe extern "C" fn(*const c_void),
}

#[repr(C)]
#[derive(Clone, Copy)]
struct RawType {
    tag: u32,
    key: u64,
}

#[repr(C)]
#[derive(Clone, Copy)]
struct Input {
    present: u32,
    value: RawValue,
}

#[repr(C)]
#[derive(Clone, Copy)]
union Payload {
    value: RawValue,
    error: OwnedBytes,
    decision: u8,
    bytes: Bytes,
    graph: Header,
}

#[repr(C)]
struct Outcome {
    tag: u32,
    payload: Payload,
}

#[repr(C)]
#[derive(Clone, Copy)]
struct Header {
    version: u32,
    size: usize,
    context: *const c_void,
    functions: *const GraphFunctions,
}
#[repr(C)]
struct Prefix {
    version: u32,
    size: usize,
}
#[repr(C)]
struct GraphFunctions {
    size: usize,
    provider: unsafe extern "C" fn() -> usize,
    describe: unsafe extern "C" fn(*const c_void) -> Bytes,
    execute: unsafe extern "C" fn(*const c_void, usize, *const Input, usize, u32, Bytes) -> Outcome,
    evaluate: unsafe extern "C" fn(*const c_void, usize, *const Input, usize) -> Outcome,
    project: unsafe extern "C" fn(*const c_void, usize) -> Outcome,
    release: unsafe extern "C" fn(*const c_void),
}
