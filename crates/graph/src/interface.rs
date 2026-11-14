use std::collections::{BTreeMap, HashSet};

use rivlet::NodeIdentity;

use crate::{Graph, GraphError, Node};

/// An anonymous endpoint is distinct from every user-defined name.
#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub enum PortName {
    Anonymous,
    Named(String),
}

impl PortName {
    #[must_use]
    pub fn as_option(&self) -> Option<&str> {
        match self {
            Self::Anonymous => None,
            Self::Named(name) => Some(name),
        }
    }
}

impl From<String> for PortName {
    fn from(name: String) -> Self {
        Self::Named(name)
    }
}
impl From<&str> for PortName {
    fn from(name: &str) -> Self {
        Self::Named(name.to_owned())
    }
}
impl From<&String> for PortName {
    fn from(name: &String) -> Self {
        Self::Named(name.clone())
    }
}
impl From<&PortName> for PortName {
    fn from(name: &PortName) -> Self {
        name.clone()
    }
}
impl From<Option<String>> for PortName {
    fn from(name: Option<String>) -> Self {
        name.map_or(Self::Anonymous, Self::Named)
    }
}
impl std::fmt::Display for PortName {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Anonymous => f.write_str("<anonymous>"),
            Self::Named(name) => f.write_str(name),
        }
    }
}

/// Input reference bound to one actual graph declaration.
#[derive(Clone, Debug, Eq, Hash, PartialEq)]
pub struct InputPort {
    owner: NodeIdentity,
    name: PortName,
}

impl InputPort {
    #[must_use]
    pub const fn owner(&self) -> NodeIdentity {
        self.owner
    }
    #[must_use]
    pub const fn name(&self) -> &PortName {
        &self.name
    }
}

/// Output reference bound to one actual graph declaration.
#[derive(Clone, Debug, Eq, Hash, PartialEq)]
pub struct OutputPort {
    owner: NodeIdentity,
    name: PortName,
}

impl OutputPort {
    #[must_use]
    pub const fn owner(&self) -> NodeIdentity {
        self.owner
    }
    #[must_use]
    pub const fn name(&self) -> &PortName {
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
    inputs: BTreeMap<PortName, Vec<InputEndpoint>>,
    outputs: BTreeMap<PortName, OutputEndpoint>,
}

impl GraphInterface {
    /// Adds one nonempty consumer group without replacing an existing declaration.
    ///
    /// # Errors
    /// Returns an error for invalid/duplicate names, empty or duplicate consumers.
    pub fn input<T: Into<InputEndpoint>>(
        mut self,
        name: impl Into<PortName>,
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
        name: impl Into<PortName>,
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
    pub const fn inputs(&self) -> &BTreeMap<PortName, Vec<InputEndpoint>> {
        &self.inputs
    }
    #[must_use]
    pub const fn outputs(&self) -> &BTreeMap<PortName, OutputEndpoint> {
        &self.outputs
    }
}

fn checked_name(name: PortName) -> Result<PortName, GraphError> {
    if let PortName::Named(value) = &name {
        if value.is_empty() || value.trim() != value {
            return Err(GraphError::InvalidPortName(value.clone()));
        }
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
    pub fn input(&self, name: impl Into<PortName>) -> Result<InputPort, GraphError> {
        let name = name.into();
        if !self
            .graph
            .structure()
            .interface()
            .inputs()
            .contains_key(&name)
        {
            return Err(GraphError::UnknownInputPort(name));
        }
        Ok(InputPort {
            owner: self.graph.id(),
            name,
        })
    }

    /// Returns an output bound to this graph's identity.
    ///
    /// # Errors
    /// Returns an error if this output is not declared.
    pub fn output(&self, name: impl Into<PortName>) -> Result<OutputPort, GraphError> {
        let name = name.into();
        if !self
            .graph
            .structure()
            .interface()
            .outputs()
            .contains_key(&name)
        {
            return Err(GraphError::UnknownOutputPort(name));
        }
        Ok(OutputPort {
            owner: self.graph.id(),
            name,
        })
    }
}
