use std::future::{Future, poll_fn};
use std::marker::PhantomData;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::task::Poll;

use action::{ActionError, ActionInputs, Value};

use super::ConditionFuture;

pub(super) trait AsyncPredicate: Send + Sync {
    fn start(&self, inputs: &[Option<Value>]) -> Result<ConditionFuture, ActionError>;
}

pub(super) struct TypedAsyncPredicate<I, F> {
    function: F,
    input: PhantomData<fn(I)>,
}

impl<I, F> TypedAsyncPredicate<I, F> {
    pub(super) const fn new(function: F) -> Self {
        Self {
            function,
            input: PhantomData,
        }
    }
}

impl<I, F, Fut> AsyncPredicate for TypedAsyncPredicate<I, F>
where
    I: ActionInputs,
    F: Fn(I) -> Fut + Send + Sync + 'static,
    Fut: Future<Output = Result<bool, ActionError>> + Send + 'static,
{
    fn start(&self, inputs: &[Option<Value>]) -> Result<ConditionFuture, ActionError> {
        let future = (self.function)(I::decode(inputs)?);
        Ok(Box::pin(async move {
            let mut future = std::pin::pin!(future);
            // 每次 poll 都捕获 panic；同步工厂调用由外层捕获，不多包一层 Box。
            poll_fn(move |cx| {
                catch_unwind(AssertUnwindSafe(|| future.as_mut().poll(cx))).unwrap_or_else(|_| {
                    Poll::Ready(Err(ActionError::new("native predicate future panicked")))
                })
            })
            .await
        }))
    }
}
