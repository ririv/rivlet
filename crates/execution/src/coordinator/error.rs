use action::ActionError;
use rivlet::SessionError;
use std::error::Error;
use std::fmt::{self, Display, Formatter};

/// Infrastructure and binding errors; per-node failures travel in the returned batch.
#[derive(Debug)]
pub enum CoordinationError {
    Session(SessionError),
    Executor(ActionError),
    Binding(ActionError),
    CapacityExceeded,
    InvalidCompletion,
}

impl Display for CoordinationError {
    fn fmt(&self, f: &mut Formatter<'_>) -> fmt::Result {
        match self {
            Self::Session(error) => Display::fmt(error, f),
            Self::Executor(error) | Self::Binding(error) => Display::fmt(error, f),
            Self::CapacityExceeded => f.write_str("native batch exceeded action dispatch capacity"),
            Self::InvalidCompletion => {
                f.write_str("native completion count exceeds submitted action count")
            }
        }
    }
}
impl Error for CoordinationError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Session(error) => Some(error),
            Self::Executor(error) | Self::Binding(error) => Some(error),
            _ => None,
        }
    }
}
impl From<SessionError> for CoordinationError {
    fn from(error: SessionError) -> Self {
        Self::Session(error)
    }
}
