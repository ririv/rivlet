//! Rust-owned graph actions and their execution contracts.

mod cancellation;
mod executor;
mod process;
mod value;

use std::error::Error;
use std::fmt::{self, Debug, Display, Formatter};
use std::path::{Path, PathBuf};
use std::sync::Arc;

pub use cancellation::{CancellationRegistration, CancellationSource, CancellationToken};
pub use process::ProcessAction;
pub use value::{ActionInputs, ExternalValue, Value, ValueType, ValueTypeId};

/// Immutable inputs available to a native action during one execution.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ActionContext {
    component_log_path: Option<PathBuf>,
    cancellation: CancellationToken,
}

impl ActionContext {
    #[must_use]
    pub fn new(component_log_path: Option<PathBuf>) -> Self {
        Self {
            component_log_path,
            cancellation: CancellationToken::default(),
        }
    }

    #[must_use]
    pub fn with_cancellation(mut self, token: CancellationToken) -> Self {
        self.cancellation = token;
        self
    }

    #[must_use]
    pub fn control(&self) -> &CancellationToken {
        &self.cancellation
    }

    #[must_use]
    pub fn component_log_path(&self) -> Option<&Path> {
        self.component_log_path.as_deref()
    }
}

/// A Rust-owned step action whose implementation never calls back into Python.
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

type DynamicFunction =
    dyn Fn(&ActionContext, &[Option<Value>]) -> Result<Value, ActionError> + Send + Sync;

struct FunctionAction {
    inputs: Vec<ValueType>,
    output: ValueType,
    function: Box<DynamicFunction>,
}

impl ErasedAction for FunctionAction {
    fn execute(
        &self,
        context: &ActionContext,
        inputs: &[Option<Value>],
    ) -> Result<Value, ActionError> {
        if inputs.len() != self.inputs.len() {
            return Err(ActionError::new("native input group has an invalid length"));
        }
        for (value, expected) in inputs.iter().zip(&self.inputs) {
            if value
                .as_ref()
                .is_some_and(|value| value.type_id() != expected.id())
            {
                return Err(ActionError::new(
                    "native input value type does not match its declaration",
                ));
            }
        }
        let value = (self.function)(context, inputs)?;
        if value.type_id() != self.output.id() {
            return Err(ActionError::new(
                "native output value type does not match its declaration",
            ));
        }
        Ok(value)
    }
    fn input_types(&self) -> &[ValueType] {
        &self.inputs
    }
    fn output_type(&self) -> ValueType {
        self.output
    }
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
    /// Declares a dynamically typed adapter action with a complete checked signature.
    ///
    /// Experimental adapter entry used by mixed execution and the extension SDK.
    #[must_use]
    pub fn from_fn(
        inputs: Vec<ValueType>,
        output: ValueType,
        function: impl Fn(&ActionContext, &[Option<Value>]) -> Result<Value, ActionError>
        + Send
        + Sync
        + 'static,
    ) -> Self {
        Self(Arc::new(FunctionAction {
            inputs,
            output,
            function: Box::new(function),
        }))
    }
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
pub enum ActionError {
    Failed(String),
    Cancelled,
}

impl ActionError {
    #[must_use]
    pub fn new(message: impl Into<String>) -> Self {
        Self::Failed(message.into())
    }
}

impl Display for ActionError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        match self {
            Self::Failed(message) => formatter.write_str(message),
            Self::Cancelled => formatter.write_str("execution cancelled"),
        }
    }
}

impl Error for ActionError {}
pub use executor::{ActionCompletion, ActionExecutor, CompletionWait};
