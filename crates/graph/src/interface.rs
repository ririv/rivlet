use std::collections::{BTreeMap, HashSet};
use std::sync::Arc;

use rivlet::NodeIdentity;

use crate::{Graph, GraphError, Node};

/// Input reference bound to one actual graph declaration.
#[derive(Clone, Debug, Eq, Hash, PartialEq)]
pub struct InputPort {
    owner: NodeIdentity,
    name: Arc<str>,
}

impl InputPort {
    #[must_use]
    pub const fn owner(&self) -> NodeIdentity {
        self.owner
    }
    #[must_use]
    pub fn name(&self) -> &str {
        &self.name
    }
}

/// Output reference bound to one actual graph declaration.
#[derive(Clone, Debug, Eq, Hash, PartialEq)]
pub struct OutputPort {
    owner: NodeIdentity,
    name: Arc<str>,
}

impl OutputPort {
    #[must_use]
    pub const fn owner(&self) -> NodeIdentity {
        self.owner
    }
    #[must_use]
    pub fn name(&self) -> &str {
        &self.name
    }
}

/// An internal consumer or an explicitly forwarded child input.
#[derive(Clone, Debug, Eq, Hash, PartialEq)]
pub enum InputEndpoint {
    Node(NodeIdentity),
    Port(InputPort),
}

/// An internal provider or an explicitly forwarded child output.
#[derive(Clone, Debug, Eq, Hash, PartialEq)]
pub enum OutputEndpoint {
    Node(NodeIdentity),
    Port(OutputPort),
}

impl<T: Node + ?Sized> From<&T> for InputEndpoint {
    fn from(value: &T) -> Self {
        Self::Node(value.id())
    }
}
impl<T: Node + ?Sized> From<&T> for OutputEndpoint {
    fn from(value: &T) -> Self {
        Self::Node(value.id())
    }
}
impl From<InputPort> for InputEndpoint {
    fn from(value: InputPort) -> Self {
        Self::Port(value)
    }
}
impl From<OutputPort> for OutputEndpoint {
    fn from(value: OutputPort) -> Self {
        Self::Port(value)
    }
}

/// Immutable interface declarations; chaining rejects duplicate names, never overwrites.
#[derive(Clone, Debug, Default)]
pub struct GraphInterface {
    inputs: BTreeMap<String, Vec<InputEndpoint>>,
    outputs: BTreeMap<String, OutputEndpoint>,
}

impl GraphInterface {
    /// Adds one nonempty consumer group without replacing an existing declaration.
    ///
    /// # Errors
    /// Returns an error for invalid/duplicate names, empty or duplicate consumers.
    pub fn input<T: Into<InputEndpoint>>(
        mut self,
        name: impl Into<String>,
        targets: impl IntoIterator<Item = T>,
    ) -> Result<Self, GraphError> {
        let name = checked_name(name.into())?;
        if self.inputs.contains_key(&name) {
            return Err(GraphError::DuplicatePort(name));
        }
        let targets: Vec<_> = targets.into_iter().map(Into::into).collect();
        if targets.is_empty() {
            return Err(GraphError::EmptyInputPort(name));
        }
        if targets.iter().collect::<HashSet<_>>().len() != targets.len() {
            return Err(GraphError::DuplicateInput(name));
        }
        self.inputs.insert(name, targets);
        Ok(self)
    }

    /// Adds one output without replacing an existing declaration.
    ///
    /// # Errors
    /// Returns an error for invalid or duplicate names.
    pub fn output(
        mut self,
        name: impl Into<String>,
        source: impl Into<OutputEndpoint>,
    ) -> Result<Self, GraphError> {
        let name = checked_name(name.into())?;
        if self.outputs.contains_key(&name) {
            return Err(GraphError::DuplicatePort(name));
        }
        self.outputs.insert(name, source.into());
        Ok(self)
    }

    #[must_use]
    pub const fn inputs(&self) -> &BTreeMap<String, Vec<InputEndpoint>> {
        &self.inputs
    }
    #[must_use]
    pub const fn outputs(&self) -> &BTreeMap<String, OutputEndpoint> {
        &self.outputs
    }
}

fn checked_name(name: String) -> Result<String, GraphError> {
    if name.is_empty() || name.trim() != name {
        return Err(GraphError::InvalidPortName(name));
    }
    Ok(name)
}

/// Port lookup on an actual Graph, never available on a Step.
pub struct GraphInterfaceView<'a> {
    graph: &'a Graph,
}

impl<'a> GraphInterfaceView<'a> {
    pub(crate) const fn new(graph: &'a Graph) -> Self {
        Self { graph }
    }

    /// Returns an input bound to this graph's identity.
    ///
    /// # Errors
    /// Returns an error if this input is not declared.
    pub fn input(&self, name: &str) -> Result<InputPort, GraphError> {
        if !self
            .graph
            .structure()
            .interface()
            .inputs()
            .contains_key(name)
        {
            return Err(GraphError::UnknownInputPort(name.to_owned()));
        }
        Ok(InputPort {
            owner: self.graph.id(),
            name: name.into(),
        })
    }

    /// Returns an output bound to this graph's identity.
    ///
    /// # Errors
    /// Returns an error if this output is not declared.
    pub fn output(&self, name: &str) -> Result<OutputPort, GraphError> {
        if !self
            .graph
            .structure()
            .interface()
            .outputs()
            .contains_key(name)
        {
            return Err(GraphError::UnknownOutputPort(name.to_owned()));
        }
        Ok(OutputPort {
            owner: self.graph.id(),
            name: name.into(),
        })
    }
}
