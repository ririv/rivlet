use action::{ActionCompletion, ActionError, ActionExecutor, CompletionWait, Value};
use rivlet::NodeId;

use super::{ActionBinding, ActionDispatch, CoordinationError, Workers};

pub(super) enum ActionPool {
    Absent,
    Active {
        executor: ActionExecutor,
        bindings: Vec<ActionDispatch>,
        running: usize,
    },
}

impl ActionPool {
    pub(super) fn new(
        bindings: Vec<ActionDispatch>,
        workers: Workers,
    ) -> Result<Self, CoordinationError> {
        let count = bindings
            .iter()
            .filter(|binding| matches!(binding, ActionDispatch::Native(_)))
            .count();
        if count == 0 {
            return Ok(Self::Absent);
        }
        let capacity = match workers {
            Workers::Fixed(capacity) => capacity.get(),
            Workers::Automatic => std::thread::available_parallelism()
                .map_err(|error| {
                    CoordinationError::Executor(ActionError::new(format!(
                        "failed to determine native action parallelism: {error}"
                    )))
                })?
                .get()
                .min(32),
        }
        .min(count);
        Ok(Self::Active {
            executor: ActionExecutor::new(capacity).map_err(CoordinationError::Executor)?,
            bindings,
            running: 0,
        })
    }

    pub(super) fn binding(&self, node: NodeId) -> Option<&ActionBinding> {
        match self {
            Self::Absent => None,
            Self::Active { bindings, .. } => match &bindings[node.index()] {
                ActionDispatch::Host => None,
                ActionDispatch::Native(binding) => Some(binding),
            },
        }
    }

    pub(super) fn submit(
        &mut self,
        node: NodeId,
        values: Vec<Option<Value>>,
    ) -> Result<(), CoordinationError> {
        let Self::Active {
            executor,
            bindings,
            running,
        } = self
        else {
            return Err(CoordinationError::InvalidCompletion);
        };
        let ActionDispatch::Native(binding) = &bindings[node.index()] else {
            return Err(CoordinationError::InvalidCompletion);
        };
        executor
            .submit(
                node.index(),
                binding.action.clone(),
                binding.context.clone(),
                values,
            )
            .map_err(CoordinationError::Executor)?;
        *running += 1;
        Ok(())
    }

    pub(super) fn drain(&mut self) -> Result<Vec<ActionCompletion>, CoordinationError> {
        match self {
            Self::Absent => Ok(vec![]),
            Self::Active {
                executor, running, ..
            } => {
                let completed = executor.drain_completions();
                *running = running
                    .checked_sub(completed.len())
                    .ok_or(CoordinationError::InvalidCompletion)?;
                Ok(completed)
            }
        }
    }

    pub(super) fn running(&self) -> usize {
        match self {
            Self::Absent => 0,
            Self::Active { running, .. } => *running,
        }
    }
    pub(super) fn prepare_wait(&self) -> Option<CompletionWait> {
        match self {
            Self::Absent | Self::Active { running: 0, .. } => None,
            Self::Active { executor, .. } => Some(executor.prepare_wait()),
        }
    }
}
