//! Rust-owned graph actions and their execution contracts.

mod executor;
mod process;
mod value;

use std::error::Error;
use std::fmt::{self, Debug, Display, Formatter};
use std::path::{Path, PathBuf};
use std::sync::Arc;

pub use process::ProcessAction;
pub use value::{ActionInputs, Value, ValueType};

/// Immutable inputs available to a native action during one execution.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ActionContext {
    component_log_path: Option<PathBuf>,
}

impl ActionContext {
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
pub trait Action: Send + Sync + 'static {
    type Inputs: ActionInputs;
    type Output: Send + Sync + 'static;
    /// Executes this action once.
    ///
    /// # Errors
    /// Returns an action-owned error when execution fails.
    fn execute(
        &self,
        context: &ActionContext,
        inputs: Self::Inputs,
    ) -> Result<Self::Output, ActionError>;
}

trait ErasedAction: Send + Sync {
    fn execute(
        &self,
        context: &ActionContext,
        inputs: &[Option<Value>],
    ) -> Result<Value, ActionError>;
    fn input_types(&self) -> &[ValueType];
    fn output_type(&self) -> ValueType;
}

struct ActionAdapter<A> {
    action: A,
    input_types: Vec<ValueType>,
}

impl<A: Action> ErasedAction for ActionAdapter<A> {
    fn execute(
        &self,
        context: &ActionContext,
        inputs: &[Option<Value>],
    ) -> Result<Value, ActionError> {
        if inputs.len() != self.input_types.len() {
            return Err(ActionError::new(format!(
                "native action expects {} result inputs, received {}",
                self.input_types.len(),
                inputs.len()
            )));
        }
        self.action
            .execute(context, A::Inputs::decode(inputs)?)
            .map(Value::new)
    }
    fn input_types(&self) -> &[ValueType] {
        &self.input_types
    }
    fn output_type(&self) -> ValueType {
        ValueType::of::<A::Output>()
    }
}

/// Type-erased, cloneable ownership of one native action implementation.
#[derive(Clone)]
pub struct ActionHandle(Arc<dyn ErasedAction>);

impl ActionHandle {
    #[must_use]
    pub fn new<A: Action>(action: A) -> Self {
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
        context: &ActionContext,
        inputs: &[Option<Value>],
    ) -> Result<Value, ActionError> {
        self.0.execute(context, inputs)
    }

    #[must_use]
    pub fn input_types(&self) -> &[ValueType] {
        self.0.input_types()
    }
    #[must_use]
    pub fn output_type(&self) -> ValueType {
        self.0.output_type()
    }
}

impl Debug for ActionHandle {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter.write_str("ActionHandle(..)")
    }
}

/// Stable error value crossing native action and language-adapter boundaries.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ActionError {
    message: String,
}

impl ActionError {
    #[must_use]
    pub fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

impl Display for ActionError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl Error for ActionError {}
pub use executor::{ActionCompletion, ActionExecutor, CompletionWait};
