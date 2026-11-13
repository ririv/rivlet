//! Rust-owned graph actions and their execution contracts.

mod executor;
mod process;
mod value;

use std::error::Error;
use std::fmt::{self, Debug, Display, Formatter};
use std::path::{Path, PathBuf};
use std::sync::Arc;

pub use process::ProcessAction;
pub use value::{NativeInputs, NativeValue, NativeValueType};

/// Immutable inputs available to a native action during one execution.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct NativeActionContext {
    component_log_path: Option<PathBuf>,
}

impl NativeActionContext {
    #[must_use]
    pub fn new(component_log_path: Option<PathBuf>) -> Self {
        Self { component_log_path }
    }

    #[must_use]
    pub fn component_log_path(&self) -> Option<&Path> {
        self.component_log_path.as_deref()
    }
}

/// A Rust-owned leaf action whose implementation never calls back into Python.
pub trait NativeAction: Send + Sync + 'static {
    type Inputs: NativeInputs;
    type Output: Send + Sync + 'static;
    /// Executes this action once.
    ///
    /// # Errors
    /// Returns an action-owned error when execution fails.
    fn execute(
        &self,
        context: &NativeActionContext,
        inputs: Self::Inputs,
    ) -> Result<Self::Output, NativeActionError>;
}

trait ErasedAction: Send + Sync {
    fn execute(
        &self,
        context: &NativeActionContext,
        inputs: &[Option<NativeValue>],
    ) -> Result<NativeValue, NativeActionError>;
    fn input_types(&self) -> &[NativeValueType];
    fn output_type(&self) -> NativeValueType;
}

struct ActionAdapter<A> {
    action: A,
    input_types: Vec<NativeValueType>,
}

impl<A: NativeAction> ErasedAction for ActionAdapter<A> {
    fn execute(
        &self,
        context: &NativeActionContext,
        inputs: &[Option<NativeValue>],
    ) -> Result<NativeValue, NativeActionError> {
        if inputs.len() != self.input_types.len() {
            return Err(NativeActionError::new(format!(
                "native action expects {} result inputs, received {}",
                self.input_types.len(),
                inputs.len()
            )));
        }
        self.action
            .execute(context, A::Inputs::decode(inputs)?)
            .map(NativeValue::new)
    }
    fn input_types(&self) -> &[NativeValueType] {
        &self.input_types
    }
    fn output_type(&self) -> NativeValueType {
        NativeValueType::of::<A::Output>()
    }
}

/// Type-erased, cloneable ownership of one native action implementation.
#[derive(Clone)]
pub struct NativeActionHandle(Arc<dyn ErasedAction>);

impl NativeActionHandle {
    #[must_use]
    pub fn new<A: NativeAction>(action: A) -> Self {
        Self(Arc::new(ActionAdapter {
            action,
            input_types: A::Inputs::types(),
        }))
    }

    /// Executes the referenced action.
    ///
    /// # Errors
    /// Returns the concrete action failure without changing its message.
    pub fn execute(
        &self,
        context: &NativeActionContext,
        inputs: &[Option<NativeValue>],
    ) -> Result<NativeValue, NativeActionError> {
        self.0.execute(context, inputs)
    }

    #[must_use]
    pub fn input_types(&self) -> &[NativeValueType] {
        self.0.input_types()
    }
    #[must_use]
    pub fn output_type(&self) -> NativeValueType {
        self.0.output_type()
    }
}

impl Debug for NativeActionHandle {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter.write_str("NativeActionHandle(..)")
    }
}

/// Stable error value crossing native action and language-adapter boundaries.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NativeActionError {
    message: String,
}

impl NativeActionError {
    #[must_use]
    pub fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

impl Display for NativeActionError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl Error for NativeActionError {}
pub use executor::{NativeActionCompletion, NativeActionExecutor, NativeCompletionWait};
