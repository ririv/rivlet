//! Shared subtree admission for Python execution sessions.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::num::NonZeroUsize;

/// Shared admission counters owned by one Python execution driver.
#[pyclass(frozen, module = "rivlet._native", name = "_ConcurrencyLimits")]
pub(crate) struct ConcurrencyLimits {
    pub(crate) limits: rivlet_core::ConcurrencyLimits,
}

#[pymethods]
impl ConcurrencyLimits {
    #[new]
    fn new(maxima: Vec<usize>) -> PyResult<Self> {
        let maxima = maxima
            .into_iter()
            .map(|maximum| {
                NonZeroUsize::new(maximum)
                    .ok_or_else(|| PyValueError::new_err("concurrency limits must be positive"))
            })
            .collect::<PyResult<Vec<_>>>()?;
        Ok(Self {
            limits: rivlet_core::ConcurrencyLimits::new(maxima),
        })
    }

    fn revision(&self) -> u64 {
        self.limits.revision()
    }
}
