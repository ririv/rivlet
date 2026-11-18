use std::fmt::{self, Debug, Formatter};
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::Arc;

use crate::action::{ActionError, ActionInputs, Value, ValueType};

use super::{GraphError, OutputEndpoint};

trait Predicate: Send + Sync {
    fn evaluate(&self, inputs: &[Option<Value>]) -> Result<bool, ActionError>;
}

struct TypedPredicate<I, F> {
    function: F,
    input: std::marker::PhantomData<fn(I)>,
}

impl<I, F> Predicate for TypedPredicate<I, F>
where
    I: ActionInputs,
    F: Fn(I) -> Result<bool, ActionError> + Send + Sync + 'static,
{
    fn evaluate(&self, inputs: &[Option<Value>]) -> Result<bool, ActionError> {
        (self.function)(I::decode(inputs)?)
    }
}

struct Declaration {
    predicate: Box<dyn Predicate>,
    types: Vec<ValueType>,
    inputs: Vec<OutputEndpoint>,
}

/// A synchronous typed predicate and its ordered graph-local result sources.
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
        let types = I::types();
        let inputs: Vec<_> = inputs.into_iter().collect();
        if types.len() != inputs.len() {
            return Err(GraphError::ConditionInputCount {
                expected: types.len(),
                actual: inputs.len(),
            });
        }
        Ok(Self(Arc::new(Declaration {
            predicate: Box::new(TypedPredicate {
                function: predicate,
                input: std::marker::PhantomData,
            }),
            types,
            inputs,
        })))
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
        catch_unwind(AssertUnwindSafe(|| self.0.predicate.evaluate(inputs)))
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
