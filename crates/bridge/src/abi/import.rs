use std::any::Any;
use std::collections::HashMap;
use std::ffi::c_void;
use std::mem::size_of;
use std::ptr;
use std::sync::Arc;

use super::values::{OwnedValue, export_value, functions, parse_type, take_error};
use super::{ABI_VERSION, Bytes, GraphFunctions, Header, Input, Prefix, RawValue};
use crate::{NodeKind, Snapshot, types};
use action::{ActionContext, ActionError, ActionHandle, Value};

/// Imported metadata and callbacks retain their original capsule/library owner.
pub struct ImportedGraph {
    header: Header,
    snapshot: Arc<Snapshot>,
    _owner: Arc<dyn Any + Send + Sync>,
    identities: Vec<rivlet::NodeIdentity>,
}
// 指针仅通过约定线程安全、非展开的 C ABI 调用；所有权由 _owner 固定到最后一个值释放。
unsafe impl Send for ImportedGraph {}
unsafe impl Sync for ImportedGraph {}

impl ImportedGraph {
    /// Imports a header without ever interpreting an exporter-owned Rust object.
    ///
    /// # Safety
    /// The pointer must address a readable C-layout ABI prefix. If compatible, its
    /// full header, callback table and referenced buffers must obey this ABI.
    /// The owner must keep the export and its library alive through all callbacks
    /// and value drops. SDK capsules use `ABI_NAME` and an `ExportedGraph` owner.
    ///
    /// # Errors
    /// Rejects incompatible headers/callbacks and malformed graph descriptions.
    pub unsafe fn from_raw(
        pointer: *const c_void,
        owner: Arc<dyn Any + Send + Sync>,
    ) -> Result<Arc<Self>, ActionError> {
        let mut declarations = HashMap::new();
        unsafe {
            Self::load(pointer, owner, |identity| {
                Ok(*declarations
                    .entry(identity)
                    .or_insert_with(rivlet::NodeIdentity::allocate))
            })
        }
        .map(Arc::new)
    }

    unsafe fn load(
        pointer: *const c_void,
        owner: Arc<dyn Any + Send + Sync>,
        mut identity: impl FnMut(u64) -> Result<rivlet::NodeIdentity, ActionError>,
    ) -> Result<Self, ActionError> {
        if pointer.is_null() {
            return Err(ActionError::new("native graph export is null"));
        }
        let prefix = unsafe { &*pointer.cast::<Prefix>() };
        if prefix.version != ABI_VERSION || prefix.size != size_of::<Header>() {
            return Err(ActionError::new("incompatible native graph ABI"));
        }
        let header = unsafe { *pointer.cast::<Header>() };
        if header.context.is_null() || header.functions.is_null() {
            return Err(ActionError::new(
                "native graph export has null ownership or callbacks",
            ));
        }
        if unsafe { *header.functions.cast::<usize>() } != size_of::<GraphFunctions>() {
            return Err(ActionError::new("incompatible native graph callback table"));
        }
        let bytes = unsafe { ((*header.functions).describe)(header.context) };
        let snapshot: Snapshot =
            serde_json::from_slice(unsafe { bytes.slice() }).map_err(|error| {
                ActionError::new(format!("invalid native graph descriptor: {error}"))
            })?;
        snapshot.validate()?;
        let identities = snapshot
            .nodes
            .iter()
            .map(|node| identity(node.identity))
            .collect::<Result<_, _>>()?;
        Ok(Self {
            header,
            snapshot: Arc::new(snapshot),
            _owner: owner,
            identities,
        })
    }

    #[must_use]
    pub fn snapshot(&self) -> &Snapshot {
        &self.snapshot
    }
    #[must_use]
    pub fn shared_snapshot(&self) -> Arc<Snapshot> {
        Arc::clone(&self.snapshot)
    }
    #[must_use]
    pub fn identity(&self, index: usize) -> rivlet::NodeIdentity {
        self.identities[index]
    }

    /// Reprojects one original scope, preserving its declaration identities.
    ///
    /// # Errors
    /// Returns projection, ABI, metadata, or declaration identity errors.
    pub fn project(self: &Arc<Self>, scope: usize) -> Result<Arc<Self>, ActionError> {
        let outcome = unsafe { ((*self.header.functions).project)(self.header.context, scope) };
        match outcome.tag {
            1 => Err(unsafe { take_error(outcome) }),
            4 => {
                let header = unsafe { outcome.payload.graph };
                let owner = Arc::new(ProjectedLease {
                    header,
                    _parent: Arc::clone(self),
                });
                // owner 保持新导出和原始库同时存活；返回体的索引与值均重新验证。
                let existing: HashMap<_, _> = self
                    .snapshot
                    .nodes
                    .iter()
                    .zip(&self.identities)
                    .map(|(node, id)| (node.identity, *id))
                    .collect();
                let graph = unsafe {
                    Self::load(
                        ptr::from_ref(&owner.header).cast(),
                        owner.clone(),
                        |identity| {
                            existing.get(&identity).copied().ok_or_else(|| {
                                ActionError::new(
                                    "projected native graph introduced an unknown declaration",
                                )
                            })
                        },
                    )
                }?;
                Ok(Arc::new(graph))
            }
            _ => Err(ActionError::new(
                "native graph projector returned an invalid outcome",
            )),
        }
    }
    #[must_use]
    pub fn provider(&self) -> usize {
        unsafe { ((*self.header.functions).provider)() }
    }

    /// Creates a checked action adapter; execution calls the originating library directly.
    ///
    /// # Errors
    /// Rejects an index which does not identify an action.
    pub fn action(self: &Arc<Self>, index: usize) -> Result<ActionHandle, ActionError> {
        let Some(NodeKind::Step { inputs, output }) =
            self.snapshot.nodes.get(index).map(|node| &node.kind)
        else {
            return Err(ActionError::new("native action index references no action"));
        };
        let inputs = inputs
            .iter()
            .map(|kind| crate::value_type(kind, self.provider()))
            .collect();
        let output = crate::value_type(output, self.provider());
        let graph = Arc::clone(self);
        Ok(ActionHandle::from_fn(
            inputs,
            output,
            move |context, inputs| graph.execute(index, context, inputs),
        ))
    }

    fn arguments(
        self: &Arc<Self>,
        values: &[Option<Value>],
    ) -> Result<(Vec<OwnedValue>, Vec<Input>), ActionError> {
        let mut owned = Vec::new();
        let mut inputs = Vec::with_capacity(values.len());
        for input in values {
            let raw = match input {
                None => {
                    inputs.push(Input {
                        present: 0,
                        value: RawValue {
                            context: ptr::null(),
                            functions: ptr::null(),
                        },
                    });
                    continue;
                }
                Some(value) => {
                    if let Some(foreign) = value.external_ref::<OwnedValue>() {
                        foreign.raw
                    } else {
                        let raw = export_value(value.clone())?;
                        owned.push(OwnedValue {
                            raw,
                            graph: Arc::clone(self),
                            kind: types::describe_value(value)?,
                        });
                        raw
                    }
                }
            };
            inputs.push(Input {
                present: 1,
                value: raw,
            });
        }
        Ok((owned, inputs))
    }

    fn execute(
        self: &Arc<Self>,
        index: usize,
        context: &ActionContext,
        values: &[Option<Value>],
    ) -> Result<Value, ActionError> {
        let (_owned, inputs) = self.arguments(values)?;
        let log = context
            .component_log_path()
            .map(|path| {
                path.to_str()
                    .ok_or_else(|| ActionError::new("native log path is not UTF-8"))
            })
            .transpose()?;
        let outcome = unsafe {
            ((*self.header.functions).execute)(
                self.header.context,
                index,
                inputs.as_ptr(),
                inputs.len(),
                u32::from(log.is_some()),
                Bytes::of(log.unwrap_or("").as_bytes()),
            )
        };
        match outcome.tag {
            0 => {
                let raw = unsafe { outcome.payload.value };
                let table = unsafe { functions(raw)? };
                let kind = match parse_type(unsafe { (table.kind)(raw.context) }) {
                    Ok(kind) => kind,
                    Err(error) => {
                        unsafe {
                            (table.release)(raw.context);
                        }
                        return Err(error);
                    }
                };
                Ok(Value::external(OwnedValue {
                    raw,
                    graph: Arc::clone(self),
                    kind,
                }))
            }
            1 => Err(unsafe { take_error(outcome) }),
            _ => Err(ActionError::new(
                "native action returned an invalid outcome",
            )),
        }
    }

    /// Evaluates a native predicate without invoking a Python callable.
    ///
    /// # Errors
    /// Returns input, callback, predicate, panic, or ABI errors.
    pub fn evaluate(
        self: &Arc<Self>,
        index: usize,
        values: &[Option<Value>],
    ) -> Result<bool, ActionError> {
        let (_owned, inputs) = self.arguments(values)?;
        let outcome = unsafe {
            ((*self.header.functions).evaluate)(
                self.header.context,
                index,
                inputs.as_ptr(),
                inputs.len(),
            )
        };
        match outcome.tag {
            1 => Err(unsafe { take_error(outcome) }),
            2 => match unsafe { outcome.payload.decision } {
                0 => Ok(false),
                1 => Ok(true),
                _ => Err(ActionError::new(
                    "native predicate returned an invalid boolean",
                )),
            },
            _ => Err(ActionError::new(
                "native predicate returned an invalid outcome",
            )),
        }
    }
}

struct ProjectedLease {
    header: Header,
    _parent: Arc<ImportedGraph>,
}
// 与 ImportedGraph 一样，释放仅调用创建库的 C ABI，不跨库释放 Rust 分配。
unsafe impl Send for ProjectedLease {}
unsafe impl Sync for ProjectedLease {}
impl Drop for ProjectedLease {
    fn drop(&mut self) {
        unsafe {
            ((*self.header.functions).release)(self.header.context);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn incompatible_prefix_is_rejected_before_accessing_callbacks() {
        for (version, size) in [
            (ABI_VERSION + 1, size_of::<Header>()),
            (ABI_VERSION, size_of::<Prefix>()),
        ] {
            let prefix = Prefix { version, size };
            // 只提供可读的公共前缀；不兼容时不得尝试读取完整 Header。
            let result =
                unsafe { ImportedGraph::from_raw(ptr::from_ref(&prefix).cast(), Arc::new(())) };
            assert!(result.is_err());
        }
    }
}
