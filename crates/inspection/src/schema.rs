use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct SchemaVersion;
impl Serialize for SchemaVersion {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_u8(3)
    }
}
impl<'de> Deserialize<'de> for SchemaVersion {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let version = u8::deserialize(deserializer)?;
        if version == 3 {
            Ok(Self)
        } else {
            Err(serde::de::Error::custom(
                "unsupported graph document schema; expected version 3",
            ))
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "view", rename_all = "lowercase")]
pub enum GraphDocument {
    Declaration(DeclarationDocument),
    Compiled(CompilationDocument),
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeclarationDocument {
    pub schema_version: SchemaVersion,
    pub root: String,
    pub parent: Option<String>,
    pub graphs: Vec<Scope>,
    pub nodes: Vec<Node>,
    pub edges: Vec<Edge>,
    pub conditions: Vec<Condition>,
    pub static_rules: Vec<StaticRule>,
    pub selections: Vec<Selection>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CompilationDocument {
    pub schema_version: SchemaVersion,
    pub root: String,
    pub parent: Option<String>,
    pub graphs: Vec<Scope>,
    pub nodes: Vec<Node>,
    pub edges: Vec<Edge>,
    pub conditions: Vec<CompiledCondition>,
    pub dependencies: Vec<Dependency>,
    pub excluded_nodes: Vec<String>,
    pub excluded_edges: Vec<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Scope {
    pub id: String,
    pub label: String,
    pub label_path: Vec<String>,
    pub owner: Option<String>,
    pub nodes: Vec<String>,
    pub ports: Vec<Port>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "lowercase", deny_unknown_fields)]
pub enum Port {
    Input {
        port: Option<String>,
        nodes: Vec<String>,
    },
    Output {
        port: Option<String>,
        node: String,
    },
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum ActionKind {
    Python,
    Native,
    Rust,
    Javascript,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub enum Node {
    Leaf(Step),
    Subgraph(Subgraph),
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Step {
    pub id: String,
    pub node_id: String,
    pub graph: String,
    pub label: String,
    pub label_path: Vec<String>,
    pub action_kind: ActionKind,
    pub condition: Option<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Subgraph {
    pub id: String,
    pub node_id: String,
    pub graph: String,
    pub label: String,
    pub label_path: Vec<String>,
    pub child_graph: String,
    pub condition: Option<String>,
}
impl Node {
    #[must_use]
    pub fn id(&self) -> &str {
        match self {
            Self::Leaf(v) => &v.id,
            Self::Subgraph(v) => &v.id,
        }
    }
    #[must_use]
    pub fn identity(&self) -> &str {
        match self {
            Self::Leaf(v) => &v.node_id,
            Self::Subgraph(v) => &v.node_id,
        }
    }
    #[must_use]
    pub fn graph(&self) -> &str {
        match self {
            Self::Leaf(v) => &v.graph,
            Self::Subgraph(v) => &v.graph,
        }
    }
    #[must_use]
    pub fn label(&self) -> &str {
        match self {
            Self::Leaf(v) => &v.label,
            Self::Subgraph(v) => &v.label,
        }
    }
    #[must_use]
    pub fn labels(&self) -> &[String] {
        match self {
            Self::Leaf(v) => &v.label_path,
            Self::Subgraph(v) => &v.label_path,
        }
    }
    #[must_use]
    pub fn condition(&self) -> Option<&str> {
        match self {
            Self::Leaf(v) => v.condition.as_deref(),
            Self::Subgraph(v) => v.condition.as_deref(),
        }
    }
    pub fn set_condition(&mut self, value: Option<String>) {
        match self {
            Self::Leaf(v) => v.condition = value,
            Self::Subgraph(v) => v.condition = value,
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub enum Edge {
    Order(OrderEdge),
    Conditional(ConditionalEdge),
    Result(ResultEdge),
    Port(PortEdge),
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OrderEdge {
    pub id: String,
    pub graph: String,
    pub source: String,
    pub target: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ConditionalEdge {
    pub id: String,
    pub graph: String,
    pub source: String,
    pub target: String,
    pub condition: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PortEdge {
    pub id: String,
    pub graph: String,
    pub source: String,
    pub target: String,
    pub source_port: Option<String>,
    pub target_port: Option<String>,
    pub condition: Option<String>,
    pub provider_node: String,
    pub consumer_nodes: Vec<String>,
}

#[derive(Clone, Debug)]
pub enum ResultSource {
    Direct(String),
    Port {
        owner: String,
        name: Option<String>,
        provider: String,
    },
}
#[derive(Clone, Debug)]
pub enum ResultTarget {
    Direct(String),
    Port {
        owner: String,
        name: Option<String>,
        consumers: Vec<String>,
    },
}
impl ResultSource {
    #[must_use]
    pub fn owner(&self) -> &str {
        match self {
            Self::Direct(v) => v,
            Self::Port { owner, .. } => owner,
        }
    }
}
impl ResultTarget {
    #[must_use]
    pub fn owner(&self) -> &str {
        match self {
            Self::Direct(v) => v,
            Self::Port { owner, .. } => owner,
        }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(try_from = "ResultWire", into = "ResultWire")]
pub struct ResultEdge {
    pub id: String,
    pub graph: String,
    pub source: ResultSource,
    pub target: ResultTarget,
    pub argument_index: usize,
}

#[derive(Clone, Debug, Default)]
enum Field<T> {
    #[default]
    Absent,
    Present(T),
}
impl<T> Field<T> {
    const fn absent(&self) -> bool {
        matches!(self, Self::Absent)
    }
}
impl<T: Serialize> Serialize for Field<T> {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        match self {
            Self::Absent => serializer.serialize_none(),
            Self::Present(value) => value.serialize(serializer),
        }
    }
}
impl<'de, T: Deserialize<'de>> Deserialize<'de> for Field<T> {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        T::deserialize(deserializer).map(Self::Present)
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct ResultWire {
    id: String,
    graph: String,
    source: String,
    target: String,
    argument_index: usize,
    #[serde(default, skip_serializing_if = "Field::absent")]
    source_port: Field<Option<String>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    provider_node: Option<String>,
    #[serde(default, skip_serializing_if = "Field::absent")]
    target_port: Field<Option<String>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    consumer_nodes: Option<Vec<String>>,
}
impl TryFrom<ResultWire> for ResultEdge {
    type Error = String;
    fn try_from(w: ResultWire) -> Result<Self, String> {
        let source = match (w.source_port, w.provider_node) {
            (Field::Absent, None) => ResultSource::Direct(w.source),
            (Field::Present(name), Some(provider)) => ResultSource::Port {
                owner: w.source,
                name,
                provider,
            },
            _ => return Err("result source port and provider must be declared together".into()),
        };
        let target = match (w.target_port, w.consumer_nodes) {
            (Field::Absent, None) => ResultTarget::Direct(w.target),
            (Field::Present(name), Some(consumers)) => ResultTarget::Port {
                owner: w.target,
                name,
                consumers,
            },
            _ => return Err("result target port and consumers must be declared together".into()),
        };
        Ok(Self {
            id: w.id,
            graph: w.graph,
            source,
            target,
            argument_index: w.argument_index,
        })
    }
}
impl From<ResultEdge> for ResultWire {
    fn from(v: ResultEdge) -> Self {
        let (source, source_port, provider_node) = match v.source {
            ResultSource::Direct(id) => (id, Field::Absent, None),
            ResultSource::Port {
                owner,
                name,
                provider,
            } => (owner, Field::Present(name), Some(provider)),
        };
        let (target, target_port, consumer_nodes) = match v.target {
            ResultTarget::Direct(id) => (id, Field::Absent, None),
            ResultTarget::Port {
                owner,
                name,
                consumers,
            } => (owner, Field::Present(name), Some(consumers)),
        };
        Self {
            id: v.id,
            graph: v.graph,
            source,
            target,
            argument_index: v.argument_index,
            source_port,
            provider_node,
            target_port,
            consumer_nodes,
        }
    }
}
impl Edge {
    #[must_use]
    pub fn id(&self) -> &str {
        match self {
            Self::Order(v) => &v.id,
            Self::Conditional(v) => &v.id,
            Self::Result(v) => &v.id,
            Self::Port(v) => &v.id,
        }
    }
    #[must_use]
    pub fn graph(&self) -> &str {
        match self {
            Self::Order(v) => &v.graph,
            Self::Conditional(v) => &v.graph,
            Self::Result(v) => &v.graph,
            Self::Port(v) => &v.graph,
        }
    }
    #[must_use]
    pub fn source(&self) -> &str {
        match self {
            Self::Order(v) => &v.source,
            Self::Conditional(v) => &v.source,
            Self::Result(v) => v.source.owner(),
            Self::Port(v) => &v.source,
        }
    }
    #[must_use]
    pub fn target(&self) -> &str {
        match self {
            Self::Order(v) => &v.target,
            Self::Conditional(v) => &v.target,
            Self::Result(v) => v.target.owner(),
            Self::Port(v) => &v.target,
        }
    }
    #[must_use]
    pub fn condition(&self) -> Option<&str> {
        match self {
            Self::Conditional(v) => Some(&v.condition),
            Self::Port(v) => v.condition.as_deref(),
            _ => None,
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Condition {
    pub id: String,
    pub inputs: Vec<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CompiledCondition {
    pub id: String,
    pub index: usize,
    pub inputs: Vec<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "lowercase", deny_unknown_fields)]
pub enum StaticRule {
    Node {
        node: String,
        condition: String,
    },
    Edge {
        source: String,
        target: String,
        condition: String,
    },
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(untagged)]
pub enum StaticValue {
    Known(bool),
    Opaque(String),
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Case {
    pub node: String,
    pub condition: StaticValue,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Selection {
    pub id: String,
    pub cases: Vec<Case>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "lowercase", deny_unknown_fields)]
pub enum Dependency {
    Required {
        source: String,
        target: String,
    },
    Conditional {
        source: String,
        target: String,
        condition: String,
    },
}
pub struct View<'a> {
    pub root: &'a str,
    pub parent: Option<&'a str>,
    pub graphs: &'a [Scope],
    pub nodes: &'a [Node],
    pub edges: &'a [Edge],
}
impl GraphDocument {
    #[must_use]
    pub fn view(&self) -> View<'_> {
        match self {
            Self::Declaration(v) => View {
                root: &v.root,
                parent: v.parent.as_deref(),
                graphs: &v.graphs,
                nodes: &v.nodes,
                edges: &v.edges,
            },
            Self::Compiled(v) => View {
                root: &v.root,
                parent: v.parent.as_deref(),
                graphs: &v.graphs,
                nodes: &v.nodes,
                edges: &v.edges,
            },
        }
    }
    #[must_use]
    pub fn condition_inputs(&self) -> Vec<(&str, &[String])> {
        match self {
            Self::Declaration(v) => v
                .conditions
                .iter()
                .map(|c| (c.id.as_str(), c.inputs.as_slice()))
                .collect(),
            Self::Compiled(v) => v
                .conditions
                .iter()
                .map(|c| (c.id.as_str(), c.inputs.as_slice()))
                .collect(),
        }
    }
}
