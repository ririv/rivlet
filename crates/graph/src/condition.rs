use std::fmt::{self, Debug, Formatter};
use std::future::Future;
use std::pin::Pin;

mod asynchronous;
use asynchronous::{AsyncPredicate, TypedAsyncPredicate};
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::Arc;

use action::{ActionError, ActionInputs, Value, ValueType};

use crate::{GraphError, OutputEndpoint};

trait SyncPredicate: Send + Sync {
    fn evaluate(&self, inputs: &[Option<Value>]) -> Result<bool, ActionError>;
}

struct TypedPredicate<I, F> {
    function: F,
    input: std::marker::PhantomData<fn(I)>,
}

impl<I, F> SyncPredicate for TypedPredicate<I, F>
where
    I: ActionInputs,
    F: Fn(I) -> Result<bool, ActionError> + Send + Sync + 'static,
{
    fn evaluate(&self, inputs: &[Option<Value>]) -> Result<bool, ActionError> {
        (self.function)(I::decode(inputs)?)
    }
}

/// One owned predicate evaluation, created separately for every execution.
pub type ConditionFuture =
    Pin<Box<dyn Future<Output = Result<bool, ActionError>> + Send + 'static>>;

/// An immediate decision or a future which must be driven by an asynchronous host.
#[must_use]
pub enum ConditionEvaluation {
    Ready(Result<bool, ActionError>),
    Pending(ConditionFuture),
}

enum Predicate {
    Sync(Box<dyn SyncPredicate>),
    Async(Box<dyn AsyncPredicate>),
}

struct Declaration {
    predicate: Predicate,
    types: Vec<ValueType>,
    inputs: Vec<OutputEndpoint>,
}

/// A typed synchronous or asynchronous predicate with ordered graph-local sources.
#[derive(Clone)]
pub struct Condition(Arc<Declaration>);

impl Condition {
    /// Declares a boolean decision without creating an action node.
    ///
    /// `I` reuses the ordered input decoding contract: `()` or tuples of
    /// `Option<Arc<T>>`. A skipped producer is None, not an invented value.
    ///
    /// # Errors
    /// Returns an error if source count differs from the typed input group.
    pub fn new<I, F>(
        predicate: F,
        inputs: impl IntoIterator<Item = OutputEndpoint>,
    ) -> Result<Self, GraphError>
    where
        I: ActionInputs,
        F: Fn(I) -> Result<bool, ActionError> + Send + Sync + 'static,
    {
        let (types, inputs) = checked_inputs::<I>(inputs)?;
        Ok(Self(Arc::new(Declaration {
            predicate: Predicate::Sync(Box::new(TypedPredicate {
                function: predicate,
                input: std::marker::PhantomData,
            })),
            types,
            inputs,
        })))
    }

    /// Declares a runtime-independent asynchronous predicate factory.
    ///
    /// Each evaluation owns its inputs and a fresh Future; compiling never calls the factory.
    ///
    /// # Errors
    /// Returns an error when source count differs from the typed input group.
    pub fn new_async<I, F, Fut>(
        predicate: F,
        inputs: impl IntoIterator<Item = OutputEndpoint>,
    ) -> Result<Self, GraphError>
    where
        I: ActionInputs,
        F: Fn(I) -> Fut + Send + Sync + 'static,
        Fut: Future<Output = Result<bool, ActionError>> + Send + 'static,
    {
        let (types, inputs) = checked_inputs::<I>(inputs)?;
        Ok(Self(Arc::new(Declaration {
            predicate: Predicate::Async(Box::new(TypedAsyncPredicate::<I, F>::new(predicate))),
            types,
            inputs,
        })))
    }

    #[must_use]
    pub fn is_async(&self) -> bool {
        matches!(self.0.predicate, Predicate::Async(_))
    }

    pub(crate) fn start(&self, inputs: &[Option<Value>]) -> ConditionEvaluation {
        match &self.0.predicate {
            Predicate::Sync(_) => ConditionEvaluation::Ready(self.evaluate(inputs)),
            Predicate::Async(predicate) => {
                if inputs.len() != self.0.types.len() {
                    return ConditionEvaluation::Ready(Err(ActionError::new(
                        "native predicate input group has an invalid length",
                    )));
                }
                match catch_unwind(AssertUnwindSafe(|| predicate.start(inputs)))
                    .unwrap_or_else(|_| Err(ActionError::new("native predicate factory panicked")))
                {
                    Ok(future) => ConditionEvaluation::Pending(future),
                    Err(error) => ConditionEvaluation::Ready(Err(error)),
                }
            }
        }
    }

    #[must_use]
    pub fn inputs(&self) -> &[OutputEndpoint] {
        &self.0.inputs
    }

    #[must_use]
    pub fn input_types(&self) -> &[ValueType] {
        &self.0.types
    }

    pub(crate) fn evaluate(&self, inputs: &[Option<Value>]) -> Result<bool, ActionError> {
        if inputs.len() != self.0.types.len() {
            return Err(ActionError::new(
                "native predicate input group has an invalid length",
            ));
        }
        // 与动作相同：panic 作为一次求值失败，不取消独立分支，也不修改全局 panic hook。
        let Predicate::Sync(predicate) = &self.0.predicate else {
            return Err(ActionError::new(
                "asynchronous predicate requires asynchronous execution",
            ));
        };
        catch_unwind(AssertUnwindSafe(|| predicate.evaluate(inputs)))
            .unwrap_or_else(|_| Err(ActionError::new("native predicate panicked")))
    }
}

impl Debug for Condition {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("Condition")
            .field("inputs", &self.inputs())
            .finish_non_exhaustive()
    }
}

fn checked_inputs<I: ActionInputs>(
    inputs: impl IntoIterator<Item = OutputEndpoint>,
) -> Result<(Vec<ValueType>, Vec<OutputEndpoint>), GraphError> {
    let types = I::types();
    let inputs: Vec<_> = inputs.into_iter().collect();
    if types.len() != inputs.len() {
        return Err(GraphError::ConditionInputCount {
            expected: types.len(),
            actual: inputs.len(),
        });
    }
    Ok((types, inputs))
}
