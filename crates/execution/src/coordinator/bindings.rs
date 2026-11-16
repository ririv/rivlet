use std::num::NonZeroUsize;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::Arc;

use action::{ActionContext, ActionError, ActionHandle, Value, ValueType};
use rivlet::NodeId;

/// Native worker capacity belongs to the host execution layer, not graph scheduling.
#[derive(Clone, Copy, Debug)]
pub enum Workers {
    Automatic,
    Fixed(NonZeroUsize),
}

#[derive(Clone)]
pub struct ActionBinding {
    pub(super) action: ActionHandle,
    pub(super) context: ActionContext,
    pub(super) inputs: Vec<NodeId>,
}

impl ActionBinding {
    /// Binds an action to one complete ordered group of compiled sources.
    ///
    /// # Errors
    /// Rejects a source count inconsistent with the action signature.
    pub fn new(
        action: ActionHandle,
        context: ActionContext,
        inputs: Vec<NodeId>,
    ) -> Result<Self, ActionError> {
        check_count(inputs.len(), action.input_types().len())?;
        Ok(Self {
            action,
            context,
            inputs,
        })
    }
}

#[derive(Clone)]
pub enum ActionDispatch {
    Host,
    Native(ActionBinding),
}

type Predicate = dyn Fn(&[Option<Value>]) -> Result<bool, ActionError> + Send + Sync;

/// One resolved native predicate use, independent of any language binding.
#[derive(Clone)]
pub struct ConditionBinding {
    pub(super) types: Vec<ValueType>,
    pub(super) inputs: Vec<NodeId>,
    predicate: Arc<Predicate>,
}

impl ConditionBinding {
    /// Binds a native callback to its compiled input group.
    ///
    /// # Errors
    /// Rejects a source count inconsistent with the predicate signature.
    pub fn new(
        types: Vec<ValueType>,
        inputs: Vec<NodeId>,
        predicate: impl Fn(&[Option<Value>]) -> Result<bool, ActionError> + Send + Sync + 'static,
    ) -> Result<Self, ActionError> {
        check_count(inputs.len(), types.len())?;
        Ok(Self {
            types,
            inputs,
            predicate: Arc::new(predicate),
        })
    }

    pub(super) fn evaluate(&self, values: &[Option<Value>]) -> Result<bool, ActionError> {
        catch_unwind(AssertUnwindSafe(|| (self.predicate)(values)))
            .unwrap_or_else(|_| Err(ActionError::new("native predicate panicked")))
    }
}

#[derive(Clone)]
pub enum ConditionDispatch {
    Host,
    Native(ConditionBinding),
}

fn check_count(actual: usize, expected: usize) -> Result<(), ActionError> {
    if actual != expected {
        return Err(ActionError::new(format!(
            "native callable expects {expected} result inputs, received {actual}"
        )));
    }
    Ok(())
}
