//! Rust-owned graph actions and their execution contracts.

mod asynchronous;
mod cancellation;
mod executor;
mod location;
mod process;
mod shared;
mod value;

use std::error::Error;
use std::fmt::{self, Debug, Display, Formatter};
use std::path::{Path, PathBuf};
use std::sync::Arc;

pub use asynchronous::{ActionFuture, AsyncAction};
pub use cancellation::{CancellationRegistration, CancellationSource, CancellationToken};
pub use location::NodeLocation;
pub use process::ProcessAction;
pub use shared::{SharedState, SharedStateError};
pub use value::{ActionInputs, ExternalValue, Value, ValueType, ValueTypeId};

/// Immutable capabilities and an optional host-owned shared object for an action.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ActionContext {
    component_log_path: Option<PathBuf>,
    cancellation: CancellationToken,
    shared: Option<SharedState>,
    location: Option<NodeLocation>,
}

impl ActionContext {
    #[must_use]
    pub fn new(component_log_path: Option<PathBuf>) -> Self {
        Self {
            component_log_path,
            cancellation: CancellationToken::default(),
            shared: None,
            location: None,
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

    /// Injects one explicit object; cloning a context preserves object identity.
    #[must_use]
    pub fn with_shared(mut self, state: impl Into<SharedState>) -> Self {
        self.shared = Some(state.into());
        self
    }

    /// Returns checked access to the injected object, never an action result.
    ///
    /// # Errors
    /// Fails when no object was injected or its type differs from `T`.
    pub fn shared<T: Send + Sync + 'static>(&self) -> Result<Arc<T>, SharedStateError> {
        self.shared
            .as_ref()
            .ok_or(SharedStateError::NotProvided)?
            .require::<T>()
    }

    #[must_use]
    pub fn component_log_path(&self) -> Option<&Path> {
        self.component_log_path.as_deref()
    }

    /// Binds all positional facts together for low-level execution hosts.
    #[must_use]
    pub fn with_location(mut self, location: NodeLocation) -> Self {
        self.location = Some(location);
        self
    }
    fn require_location(&self) -> Result<&NodeLocation, ActionError> {
        self.location
            .as_ref()
            .ok_or_else(|| ActionError::new("action context is not bound to a graph node"))
    }
    /// Returns the declaration identity, shared by repeated mounts.
    ///
    /// # Errors
    /// Fails for a low-level context without an explicit node binding.
    pub fn node_id(&self) -> Result<rivlet::NodeIdentity, ActionError> {
        Ok(self.require_location()?.node_id())
    }
    /// Returns this actual mount, including generated-graph ancestors.
    ///
    /// # Errors
    /// Fails for a low-level context without an explicit node binding.
    pub fn node_path(&self) -> Result<&[usize], ActionError> {
        Ok(self.require_location()?.node_path())
    }
    /// Returns display labels; these are not result or scheduling identities.
    ///
    /// # Errors
    /// Fails for a low-level context without an explicit node binding.
    pub fn label_path(&self) -> Result<&[String], ActionError> {
        Ok(self.require_location()?.label_path())
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
pub struct ActionHandle(ActionKind);

#[derive(Clone)]
enum ActionKind {
    Sync(Arc<dyn ErasedAction>),
    Async(Arc<dyn asynchronous::ErasedAsyncAction>),
}

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
        Self(ActionKind::Sync(Arc::new(FunctionAction {
            inputs,
            output,
            function: Box::new(function),
        })))
    }
    #[must_use]
    pub fn new<A: Action>(action: A) -> Self {
        Self(ActionKind::Sync(Arc::new(ActionAdapter {
            action,
            input_types: A::Inputs::types(),
        })))
    }

    #[must_use]
    pub fn new_async<A: AsyncAction>(action: A) -> Self {
        Self(ActionKind::Async(Arc::new(asynchronous::Adapter::new(
            action,
        ))))
    }

    #[must_use]
    pub const fn is_async(&self) -> bool {
        matches!(self.0, ActionKind::Async(_))
    }

    /// Creates an owned future without selecting a runtime or blocking a worker.
    ///
    /// # Errors
    /// Rejects synchronous handles and invalid input groups; catches input-decoder panics.
    pub fn start_async(
        &self,
        context: ActionContext,
        inputs: &[Option<Value>],
    ) -> Result<ActionFuture, ActionError> {
        match &self.0 {
            ActionKind::Sync(_) => Err(ActionError::new("synchronous action requires execute()")),
            ActionKind::Async(action) => {
                std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                    action.start(context, inputs)
                }))
                .unwrap_or_else(|_| {
                    Err(ActionError::new(
                        "asynchronous action input decoding panicked",
                    ))
                })
            }
        }
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
        match &self.0 {
            ActionKind::Sync(action) => action.execute(context, inputs),
            ActionKind::Async(_) => Err(ActionError::new(
                "asynchronous action requires start_async()",
            )),
        }
    }

    #[must_use]
    pub fn input_types(&self) -> &[ValueType] {
        match &self.0 {
            ActionKind::Sync(action) => action.input_types(),
            ActionKind::Async(action) => action.input_types(),
        }
    }
    #[must_use]
    pub fn output_type(&self) -> ValueType {
        match &self.0 {
            ActionKind::Sync(action) => action.output_type(),
            ActionKind::Async(action) => action.output_type(),
        }
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
