//! Runtime-independent owned asynchronous actions.

use crate::{ActionContext, ActionError, ActionInputs, Value, ValueType};
use std::future::Future;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::pin::Pin;
use std::sync::Arc;
use std::task::{Context, Poll};

/// An asynchronous action polled by the caller's runtime, not a blocking worker.
pub trait AsyncAction: Send + Sync + 'static {
    type Inputs: ActionInputs;
    type Output: Send + Sync + 'static;
    fn execute(
        &self,
        context: ActionContext,
        inputs: Self::Inputs,
    ) -> impl Future<Output = Result<Self::Output, ActionError>> + Send;
}

pub type ActionFuture = Pin<Box<dyn Future<Output = Result<Value, ActionError>> + Send + 'static>>;
pub(super) trait ErasedAsyncAction: Send + Sync {
    fn start(
        &self,
        context: ActionContext,
        inputs: &[Option<Value>],
    ) -> Result<ActionFuture, ActionError>;
    fn input_types(&self) -> &[ValueType];
    fn output_type(&self) -> ValueType;
}
pub(super) struct Adapter<A: AsyncAction> {
    action: Arc<A>,
    types: Vec<ValueType>,
}
impl<A: AsyncAction> Adapter<A> {
    pub(super) fn new(action: A) -> Self {
        Self {
            action: Arc::new(action),
            types: A::Inputs::types(),
        }
    }
}
impl<A: AsyncAction> ErasedAsyncAction for Adapter<A> {
    fn start(
        &self,
        context: ActionContext,
        inputs: &[Option<Value>],
    ) -> Result<ActionFuture, ActionError> {
        if inputs.len() != self.types.len() {
            return Err(ActionError::new(
                "asynchronous action input group has an invalid length",
            ));
        }
        let inputs = A::Inputs::decode(inputs)?;
        let action = Arc::clone(&self.action);
        Ok(Box::pin(Guarded {
            inner: Box::pin(async move {
                context.control().require_running()?;
                action.execute(context, inputs).await.map(Value::new)
            }),
        }))
    }
    fn input_types(&self) -> &[ValueType] {
        &self.types
    }
    fn output_type(&self) -> ValueType {
        ValueType::of::<A::Output>()
    }
}
struct Guarded {
    inner: ActionFuture,
}
impl Future for Guarded {
    type Output = Result<Value, ActionError>;
    fn poll(mut self: Pin<&mut Self>, context: &mut Context<'_>) -> Poll<Self::Output> {
        catch_unwind(AssertUnwindSafe(|| self.inner.as_mut().poll(context)))
            .unwrap_or_else(|_| Poll::Ready(Err(ActionError::new("asynchronous action panicked"))))
    }
}
