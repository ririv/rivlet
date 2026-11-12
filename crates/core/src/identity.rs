//! Declaration identity, independent of mounting and execution indexes.

use std::sync::atomic::{AtomicU64, Ordering};

static NEXT_STEP_ID: AtomicU64 = AtomicU64::new(1);

/// A process-local declaration identity shared by all language adapters.
#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct StepId(u64);

impl StepId {
    /// Allocates one identity in O(1), without retaining a declaration registry.
    ///
    /// # Panics
    /// Panics if the process exhausts the u64 identity space.
    #[must_use]
    pub fn allocate() -> Self {
        Self(
            NEXT_STEP_ID
                .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |value| {
                    value.checked_add(1)
                })
                .expect("step identity space exhausted"),
        )
    }

    #[must_use]
    pub const fn value(self) -> u64 {
        self.0
    }
}
