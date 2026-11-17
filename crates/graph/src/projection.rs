use std::collections::{BTreeMap, HashMap};
use std::sync::Arc;

use action::{ActionError, ActionHandle, Value, ValueType};
use rivlet::{
    CompiledExecutionGraph, ConditionId, DeclarationNode, DeclarationPrerequisite, DeclaredGraph,
    DeclaredNodeKind, GraphCompilation, GraphDeclaration, GraphId, NodeActivation, NodeId,
    NodeIdentity, PortConnection, Prerequisite, RuntimeCondition,
};

use crate::{
    Condition, Edge, Graph, GraphError, InputEndpoint, InputPort, Node, NodeKind, OutputEndpoint,
    OutputPort, PortName, ResultTarget,
};

/// Action ownership and composite membership are mutually exclusive.
#[derive(Clone, Debug)]
pub enum ProjectedKind {
    Step(ActionHandle),
    Graph(GraphId),
}

/// One mounted declaration with an action only when it is a step.
#[derive(Clone, Debug)]
pub struct ProjectedNode {
    identity: NodeIdentity,
    label: String,
    path: Vec<usize>,
    graph: GraphId,
    kind: ProjectedKind,
    prerequisites: Vec<Prerequisite>,
    inputs: Vec<NodeId>,
    condition: Option<ConditionId>,
}

impl ProjectedNode {
    #[must_use]
    pub const fn identity(&self) -> NodeIdentity {
        self.identity
    }
    #[must_use]
    pub fn label(&self) -> &str {
        &self.label
    }
    #[must_use]
    pub fn path(&self) -> &[usize] {
        &self.path
    }
    #[must_use]
    pub const fn graph(&self) -> GraphId {
        self.graph
    }
    #[must_use]
    pub const fn kind(&self) -> &ProjectedKind {
        &self.kind
    }
    #[must_use]
    pub fn prerequisites(&self) -> &[Prerequisite] {
        &self.prerequisites
    }
    #[must_use]
    pub fn inputs(&self) -> &[NodeId] {
        &self.inputs
    }
    #[must_use]
    pub const fn condition(&self) -> Option<ConditionId> {
        self.condition
    }
}

/// A predicate's actual declaration use, independent of its callable identity.
#[derive(Clone, Copy, Debug)]
pub enum PredicateSite {
    Node(NodeId),
    Edge { target: NodeId, index: usize },
}

/// One predicate use resolved in its actual mounted declaration scope.
#[derive(Clone, Debug)]
pub struct ProjectedCondition {
    declaration: Condition,
    inputs: Vec<NodeId>,
    site: PredicateSite,
}

impl ProjectedCondition {
    #[must_use]
    pub fn sources(&self) -> &[OutputEndpoint] {
        self.declaration.inputs()
    }

    #[must_use]
    pub const fn target(&self) -> NodeId {
        match self.site {
            PredicateSite::Node(node) | PredicateSite::Edge { target: node, .. } => node,
        }
    }

    #[must_use]
    pub const fn edge_index(&self) -> Option<usize> {
        match self.site {
            PredicateSite::Node(_) => None,
            PredicateSite::Edge { index, .. } => Some(index),
        }
    }

    #[must_use]
    pub fn input_types(&self) -> &[ValueType] {
        self.declaration.input_types()
    }
    #[must_use]
    pub fn inputs(&self) -> &[NodeId] {
        &self.inputs
    }

    /// Evaluates already-ready typed inputs, without copying their payloads.
    ///
    /// # Errors
    /// Returns a predicate error, input decoding failure, or caught panic.
    pub fn evaluate(&self, values: &[Option<Value>]) -> Result<bool, ActionError> {
        self.declaration.evaluate(values)
    }
}

/// One declaration scope; resolved port indexes are derived from its immutable body.
#[derive(Clone, Debug)]
pub struct ProjectedScope {
    declaration: Arc<Graph>,
    owner: Option<NodeId>,
    nodes: Vec<NodeId>,
    local: HashMap<NodeIdentity, NodeId>,
    inputs: BTreeMap<PortName, Vec<NodeId>>,
    outputs: BTreeMap<PortName, NodeId>,
}

impl ProjectedScope {
    #[must_use]
    pub const fn declaration(&self) -> &Arc<Graph> {
        &self.declaration
    }
    #[must_use]
    pub const fn owner(&self) -> Option<NodeId> {
        self.owner
    }
    #[must_use]
    pub fn nodes(&self) -> &[NodeId] {
        &self.nodes
    }
    #[must_use]
    pub const fn inputs(&self) -> &BTreeMap<PortName, Vec<NodeId>> {
        &self.inputs
    }
    #[must_use]
    pub const fn outputs(&self) -> &BTreeMap<PortName, NodeId> {
        &self.outputs
    }

    /// Resolves a direct local declaration, never guesses a descendant mount.
    ///
    /// # Errors
    /// Returns an error if the identity is not local to this scope.
    pub fn local_node(&self, identity: NodeIdentity) -> Result<NodeId, GraphError> {
        self.local
            .get(&identity)
            .copied()
            .ok_or(GraphError::UnknownNode(identity))
    }
}

/// The shared lowering input plus Rust-owned metadata, usable by language adapters.
#[derive(Clone, Debug)]
pub struct Projection {
    scopes: Vec<ProjectedScope>,
    nodes: Vec<ProjectedNode>,
    ports: Vec<PortConnection>,
    conditions: Vec<ProjectedCondition>,
}

impl Projection {
    /// Projects shared declaration while retaining every actual graph boundary.
    ///
    /// # Errors
    /// Returns an error for an invalid interface reference.
    pub fn from_graph_contents(declaration: Arc<Graph>) -> Result<Self, GraphError> {
        let mut projection = Self {
            scopes: Vec::new(),
            nodes: Vec::new(),
            ports: Vec::new(),
            conditions: Vec::new(),
        };
        projection.visit(declaration, None, &[])?;
        Ok(projection)
    }

    /// Projects an actual root node at path 0, with no synthetic executable node.
    ///
    /// # Errors
    /// Returns an error for an invalid interface reference.
    pub fn from_node(node: NodeKind) -> Result<Self, GraphError> {
        Self::from_graph_contents(Arc::new(Graph::new("<root scope>", [node], [])?))
    }

    #[must_use]
    pub fn nodes(&self) -> &[ProjectedNode] {
        &self.nodes
    }
    #[must_use]
    pub fn scopes(&self) -> &[ProjectedScope] {
        &self.scopes
    }
    #[must_use]
    pub fn port_connections(&self) -> &[PortConnection] {
        &self.ports
    }
    #[must_use]
    pub fn conditions(&self) -> &[ProjectedCondition] {
        &self.conditions
    }

    /// Uses the existing core compiler, including hierarchy and final DAG checks.
    ///
    /// # Errors
    /// Returns the common declaration error, including resolved port cycles.
    pub fn compile(self) -> Result<Compilation, GraphError> {
        for node in &self.nodes {
            if let ProjectedKind::Step(action) = &node.kind {
                if action.input_types().len() != node.inputs.len() {
                    return Err(GraphError::ResultInputCount {
                        node: node.identity,
                        expected: action.input_types().len(),
                        actual: node.inputs.len(),
                    });
                }
            }
        }
        let graph = GraphDeclaration::new(
            self.scopes.iter().map(|scope| {
                scope
                    .owner
                    .map_or(DeclaredGraph::Root, DeclaredGraph::OwnedBy)
            }),
            self.nodes.iter().map(|node| {
                DeclarationNode::new(
                    node.graph,
                    match node.kind {
                        ProjectedKind::Step(_) => DeclaredNodeKind::Step,
                        ProjectedKind::Graph(child_graph) => {
                            DeclaredNodeKind::Composite { child_graph }
                        }
                    },
                    NodeActivation::Active {
                        prerequisites: node
                            .prerequisites
                            .iter()
                            .copied()
                            .map(|prerequisite| match prerequisite {
                                Prerequisite::Required(node) => {
                                    DeclarationPrerequisite::Required(node)
                                }
                                Prerequisite::Conditional { node, condition } => {
                                    DeclarationPrerequisite::RuntimeConditional { node, condition }
                                }
                            })
                            .collect(),
                        condition: node.condition,
                    },
                )
            }),
            self.conditions
                .iter()
                .map(|condition| RuntimeCondition::new(condition.inputs.iter().copied())),
            self.ports.iter().copied(),
        )
        .compile()
        .map_err(GraphError::Declaration)?;
        Ok(Compilation {
            graph,
            projection: self,
        })
    }

    fn visit(
        &mut self,
        declaration: Arc<Graph>,
        owner: Option<NodeId>,
        prefix: &[usize],
    ) -> Result<GraphId, GraphError> {
        // M 个挂载、E 条边、P 次端口引用、R 个展开端点，L 为路径复制总长：
        // 平均 O(M+E+P*log(K+1)+R+L)，K 为最大单层端口数。
        // 共享结构保留 Arc；局部节点哈希查询平均 O(1)，具名端口用有序映射。
        let scope = GraphId::new(self.scopes.len());
        let body = Arc::clone(&declaration);
        self.scopes.push(ProjectedScope {
            declaration,
            owner,
            nodes: Vec::new(),
            local: HashMap::new(),
            inputs: BTreeMap::new(),
            outputs: BTreeMap::new(),
        });
        for (index, node) in body.nodes().iter().enumerate() {
            let id = NodeId::new(self.nodes.len());
            let mut path = prefix.to_vec();
            path.push(index);
            let kind = match node {
                NodeKind::Step(step) => ProjectedKind::Step(step.action().clone()),
                NodeKind::Graph(_) => ProjectedKind::Graph(GraphId::new(self.scopes.len())),
            };
            self.nodes.push(ProjectedNode {
                identity: node.id(),
                label: node.label().to_owned(),
                path: path.clone(),
                graph: scope,
                kind,
                prerequisites: Vec::new(),
                inputs: Vec::new(),
                condition: None,
            });
            self.scopes[scope.index()].nodes.push(id);
            self.scopes[scope.index()].local.insert(node.id(), id);
            if let NodeKind::Graph(graph) = node {
                self.visit(Arc::new(graph.clone()), Some(id), &path)?;
            }
        }
        self.resolve_scope(scope, &body)?;
        Ok(scope)
    }

    fn resolve_scope(&mut self, scope: GraphId, declaration: &Graph) -> Result<(), GraphError> {
        for node in declaration.nodes() {
            if let Some(condition) = node.condition() {
                let target = self.scopes[scope.index()].local_node(node.id())?;
                let condition =
                    self.bind_condition(scope, condition, PredicateSite::Node(target))?;
                self.nodes[target.index()].condition = Some(condition);
            }
        }
        for (edge_index, edge) in declaration.edges().iter().enumerate() {
            match edge {
                Edge::Completion { source, target } => {
                    let source = self.scopes[scope.index()].local_node(*source)?;
                    let target = self.scopes[scope.index()].local_node(*target)?;
                    self.nodes[target.index()]
                        .prerequisites
                        .push(Prerequisite::Required(source));
                }
                Edge::ConditionalCompletion {
                    source,
                    target,
                    condition,
                } => {
                    let source = self.scopes[scope.index()].local_node(*source)?;
                    let target = self.scopes[scope.index()].local_node(*target)?;
                    let condition = self.bind_condition(
                        scope,
                        condition,
                        PredicateSite::Edge {
                            target,
                            index: edge_index,
                        },
                    )?;
                    self.nodes[target.index()]
                        .prerequisites
                        .push(Prerequisite::Conditional {
                            node: source,
                            condition,
                        });
                }
                Edge::Ports { source, target } => {
                    let source = self.output_port(scope, source)?;
                    for target in self.input_port(scope, target)? {
                        self.ports.push(PortConnection::new(source, target));
                    }
                }
                Edge::ConditionalPorts {
                    source,
                    target,
                    condition,
                } => {
                    let source = self.output_port(scope, source)?;
                    let owner = self.scopes[scope.index()].local_node(target.owner())?;
                    let condition = self.bind_condition(
                        scope,
                        condition,
                        PredicateSite::Edge {
                            target: owner,
                            index: edge_index,
                        },
                    )?;
                    for target in self.input_port(scope, target)? {
                        self.ports
                            .push(PortConnection::conditional(source, target, condition));
                    }
                }
                Edge::Results { sources, target } => {
                    let sources = sources
                        .iter()
                        .map(|source| self.output_endpoint(scope, source))
                        .collect::<Result<Vec<_>, _>>()?;
                    let targets = match target {
                        ResultTarget::Step(id) => vec![self.scopes[scope.index()].local_node(*id)?],
                        ResultTarget::Port(port) => self.input_port(scope, port)?,
                    };
                    for target in targets {
                        self.bind_results(target, &sources)?;
                    }
                }
            }
        }
        for (name, targets) in declaration.interface().declaration().inputs() {
            let mut resolved = Vec::new();
            for target in targets {
                resolved.extend(self.input_endpoint(scope, target)?);
            }
            self.scopes[scope.index()]
                .inputs
                .insert(name.clone(), resolved);
        }
        for (name, source) in declaration.interface().declaration().outputs() {
            let resolved = self.output_endpoint(scope, source)?;
            self.scopes[scope.index()]
                .outputs
                .insert(name.clone(), resolved);
        }
        Ok(())
    }

    fn bind_results(&mut self, target: NodeId, sources: &[NodeId]) -> Result<(), GraphError> {
        // O(I) 校验完整参数组并登记真实依赖，不按每个输入重扫节点或子图。
        let node = &self.nodes[target.index()];
        let ProjectedKind::Step(action) = &node.kind else {
            return Err(GraphError::ResultTargetNotStep(node.identity));
        };
        if !node.inputs.is_empty() {
            return Err(GraphError::DuplicateResults(node.identity));
        }
        if action.input_types().len() != sources.len() {
            return Err(GraphError::ResultInputCount {
                node: node.identity,
                expected: action.input_types().len(),
                actual: sources.len(),
            });
        }
        for (source, expected) in sources.iter().zip(action.input_types()) {
            let actual = match &self.nodes[source.index()].kind {
                ProjectedKind::Step(action) => action.output_type(),
                ProjectedKind::Graph(_) => ValueType::of::<()>(),
            };
            if actual != *expected {
                return Err(GraphError::ResultTypeMismatch {
                    expected: expected.name(),
                    actual: actual.name(),
                });
            }
        }
        self.nodes[target.index()].inputs = sources.to_vec();
        for source in sources {
            if self.nodes[source.index()].graph == self.nodes[target.index()].graph {
                self.nodes[target.index()]
                    .prerequisites
                    .push(Prerequisite::Required(*source));
            } else {
                self.ports.push(PortConnection::new(*source, target));
            }
        }
        Ok(())
    }

    fn bind_condition(
        &mut self,
        scope: GraphId,
        declaration: &Condition,
        site: PredicateSite,
    ) -> Result<ConditionId, GraphError> {
        let inputs = declaration
            .inputs()
            .iter()
            .map(|source| self.output_endpoint(scope, source))
            .collect::<Result<Vec<_>, _>>()?;
        for (source, expected) in inputs.iter().zip(declaration.input_types()) {
            let actual = match &self.nodes[source.index()].kind {
                ProjectedKind::Step(action) => action.output_type(),
                ProjectedKind::Graph(_) => ValueType::of::<()>(),
            };
            if actual != *expected {
                return Err(GraphError::ResultTypeMismatch {
                    expected: expected.name(),
                    actual: actual.name(),
                });
            }
        }
        let id = ConditionId::new(self.conditions.len());
        self.conditions.push(ProjectedCondition {
            declaration: declaration.clone(),
            inputs,
            site,
        });
        Ok(id)
    }

    fn child_scope(&self, scope: GraphId, owner: NodeIdentity) -> Result<GraphId, GraphError> {
        let id = self.scopes[scope.index()].local_node(owner)?;
        match self.nodes[id.index()].kind {
            ProjectedKind::Graph(child) => Ok(child),
            ProjectedKind::Step(_) => Err(GraphError::ExpectedGraph(owner)),
        }
    }

    fn input_endpoint(
        &self,
        scope: GraphId,
        endpoint: &InputEndpoint,
    ) -> Result<Vec<NodeId>, GraphError> {
        match endpoint {
            InputEndpoint::Node(id) => Ok(vec![self.scopes[scope.index()].local_node(*id)?]),
            InputEndpoint::Port(port) => self.input_port(scope, port),
        }
    }
    fn output_endpoint(
        &self,
        scope: GraphId,
        endpoint: &OutputEndpoint,
    ) -> Result<NodeId, GraphError> {
        match endpoint {
            OutputEndpoint::Node(id) => self.scopes[scope.index()].local_node(*id),
            OutputEndpoint::Port(port) => self.output_port(scope, port),
        }
    }
    fn input_port(&self, scope: GraphId, port: &InputPort) -> Result<Vec<NodeId>, GraphError> {
        let child = self.child_scope(scope, port.owner())?;
        self.scopes[child.index()]
            .inputs
            .get(port.name())
            .cloned()
            .ok_or_else(|| GraphError::UnknownInputPort(port.name().to_owned()))
    }
    fn output_port(&self, scope: GraphId, port: &OutputPort) -> Result<NodeId, GraphError> {
        let child = self.child_scope(scope, port.owner())?;
        self.scopes[child.index()]
            .outputs
            .get(port.name())
            .copied()
            .ok_or_else(|| GraphError::UnknownOutputPort(port.name().to_owned()))
    }
}

/// A common compiled graph paired with the exact Rust action/identity side table.
pub struct Compilation {
    graph: GraphCompilation,
    projection: Projection,
}

impl Compilation {
    #[must_use]
    pub const fn graph(&self) -> &CompiledExecutionGraph {
        self.graph.graph()
    }
    #[must_use]
    pub const fn projection(&self) -> &Projection {
        &self.projection
    }

    /// Reads metadata using the actual compiled node mapping.
    ///
    /// # Panics
    /// Panics if the index does not belong to this compilation.
    #[must_use]
    pub fn node(&self, index: NodeId) -> &ProjectedNode {
        &self.projection.nodes[self.graph.declaration_nodes()[index.index()].index()]
    }

    /// Reads the predicate side table using the compiler's actual condition mapping.
    ///
    /// # Panics
    /// Panics if the condition does not belong to this compilation.
    #[must_use]
    pub fn condition(&self, index: ConditionId) -> &ProjectedCondition {
        &self.projection.conditions[self.graph.declaration_conditions()[index.index()].index()]
    }
}
