//! Explicit host-owned state, separate from graph declarations and action results.

use std::any::{Any, type_name};
use std::fmt;
use std::sync::Arc;

use crate::ActionError;

/// Cloneable ownership of one injected object; interior synchronization is host-owned.
#[derive(Clone)]
pub struct SharedState {
    value: Arc<dyn Any + Send + Sync>,
    name: &'static str,
}

impl<T: Send + Sync + 'static> From<Arc<T>> for SharedState {
    fn from(value: Arc<T>) -> Self {
        Self {
            value,
            name: type_name::<T>(),
        }
    }
}

impl SharedState {
    /// Returns the same object without copying or serializing its contents.
    ///
    /// # Errors
    /// Returns a type mismatch when the requested host type differs.
    pub fn require<T: Send + Sync + 'static>(&self) -> Result<Arc<T>, SharedStateError> {
        Arc::clone(&self.value)
            .downcast::<T>()
            .map_err(|_| SharedStateError::TypeMismatch {
                expected: type_name::<T>(),
                actual: self.name,
            })
    }
}

impl fmt::Debug for SharedState {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("SharedState")
            .field("type", &self.name)
            .finish_non_exhaustive()
    }
}
impl PartialEq for SharedState {
    fn eq(&self, other: &Self) -> bool {
        Arc::ptr_eq(&self.value, &other.value)
    }
}
impl Eq for SharedState {}

/// Explicit access failures; no state is created and no conversion is attempted.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum SharedStateError {
    NotProvided,
    TypeMismatch {
        expected: &'static str,
        actual: &'static str,
    },
}
impl fmt::Display for SharedStateError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::NotProvided => {
                formatter.write_str("no shared state was provided for this execution")
            }
            Self::TypeMismatch { expected, actual } => write!(
                formatter,
                "shared state type mismatch: expected {expected}, got {actual}"
            ),
        }
    }
}
impl std::error::Error for SharedStateError {}
impl From<SharedStateError> for ActionError {
    fn from(error: SharedStateError) -> Self {
        Self::new(error.to_string())
    }
}
