use std::collections::{BTreeMap, HashMap};
use std::sync::Arc;

use action::{NativeActionHandle, NativeValueType};
use rivlet::{
    CompiledExecutionGraph, DeclarationNode, DeclarationPrerequisite, DeclaredGraph,
    DeclaredNodeKind, GraphCompilation, GraphDeclaration, GraphId, NodeActivation, NodeId,
    NodeIdentity, PortConnection,
};

use crate::{
    Edge, GraphError, GraphStructure, InputEndpoint, InputPort, Node, NodeKind, OutputEndpoint,
    OutputPort, ResultTarget,
};

/// Action ownership and composite membership are mutually exclusive.
#[derive(Clone, Debug)]
pub enum ProjectedKind {
    Leaf(NativeActionHandle),
    Graph(GraphId),
}

/// One mounted declaration with an action only when it is a leaf.
#[derive(Clone, Debug)]
pub struct ProjectedNode {
    identity: NodeIdentity,
    label: String,
    path: Vec<usize>,
    graph: GraphId,
    kind: ProjectedKind,
    prerequisites: Vec<NodeId>,
    inputs: Vec<NodeId>,
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
    pub fn prerequisites(&self) -> &[NodeId] {
        &self.prerequisites
    }
    #[must_use]
    pub fn inputs(&self) -> &[NodeId] {
        &self.inputs
    }
}

/// One declaration scope; resolved port indexes are derived from its immutable body.
#[derive(Clone, Debug)]
pub struct ProjectedScope {
    structure: Arc<GraphStructure>,
    owner: Option<NodeId>,
    nodes: Vec<NodeId>,
    local: HashMap<NodeIdentity, NodeId>,
    inputs: BTreeMap<String, Vec<NodeId>>,
    outputs: BTreeMap<String, NodeId>,
}

impl ProjectedScope {
    #[must_use]
    pub const fn structure(&self) -> &Arc<GraphStructure> {
        &self.structure
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
    pub const fn inputs(&self) -> &BTreeMap<String, Vec<NodeId>> {
        &self.inputs
    }
    #[must_use]
    pub const fn outputs(&self) -> &BTreeMap<String, NodeId> {
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
}

impl Projection {
    /// Projects shared structure while retaining every actual graph boundary.
    ///
    /// # Errors
    /// Returns an error for an invalid interface reference.
    pub fn from_structure(structure: Arc<GraphStructure>) -> Result<Self, GraphError> {
        let mut projection = Self {
            scopes: Vec::new(),
            nodes: Vec::new(),
            ports: Vec::new(),
        };
        projection.visit(structure, None, &[])?;
        Ok(projection)
    }

    /// Projects an actual root node at path 0, with no synthetic executable node.
    ///
    /// # Errors
    /// Returns an error for an invalid interface reference.
    pub fn from_node(node: NodeKind) -> Result<Self, GraphError> {
        Self::from_structure(Arc::new(GraphStructure::new([node], [])?))
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

    /// Uses the existing core compiler, including hierarchy and final DAG checks.
    ///
    /// # Errors
    /// Returns the common declaration error, including resolved port cycles.
    pub fn compile(self) -> Result<Compilation, GraphError> {
        for node in &self.nodes {
            if let ProjectedKind::Leaf(action) = &node.kind {
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
                        ProjectedKind::Leaf(_) => DeclaredNodeKind::Leaf,
                        ProjectedKind::Graph(child_graph) => {
                            DeclaredNodeKind::Composite { child_graph }
                        }
                    },
                    NodeActivation::Active {
                        prerequisites: node
                            .prerequisites
                            .iter()
                            .copied()
                            .map(DeclarationPrerequisite::Required)
                            .collect(),
                        condition: None,
                    },
                )
            }),
            [],
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
        structure: Arc<GraphStructure>,
        owner: Option<NodeId>,
        prefix: &[usize],
    ) -> Result<GraphId, GraphError> {
        // M 个挂载、E 条边、P 次端口引用、R 个展开端点，L 为路径复制总长：
        // 平均 O(M+E+P*log(K+1)+R+L)，K 为最大单层端口数。
        // 共享结构保留 Arc；局部节点哈希查询平均 O(1)，具名端口用有序映射。
        let scope = GraphId::new(self.scopes.len());
        let body = Arc::clone(&structure);
        self.scopes.push(ProjectedScope {
            structure,
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
                NodeKind::Leaf(leaf) => ProjectedKind::Leaf(leaf.action().clone()),
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
            });
            self.scopes[scope.index()].nodes.push(id);
            self.scopes[scope.index()].local.insert(node.id(), id);
            if let NodeKind::Graph(graph) = node {
                self.visit(Arc::clone(graph.structure()), Some(id), &path)?;
            }
        }
        self.resolve_scope(scope, &body)?;
        Ok(scope)
    }

    fn resolve_scope(
        &mut self,
        scope: GraphId,
        structure: &GraphStructure,
    ) -> Result<(), GraphError> {
        for edge in structure.edges() {
            match edge {
                Edge::Completion { source, target } => {
                    let source = self.scopes[scope.index()].local_node(*source)?;
                    let target = self.scopes[scope.index()].local_node(*target)?;
                    self.nodes[target.index()].prerequisites.push(source);
                }
                Edge::Ports { source, target } => {
                    let source = self.output_port(scope, source)?;
                    for target in self.input_port(scope, target)? {
                        self.ports.push(PortConnection::new(source, target));
                    }
                }
                Edge::Results { sources, target } => {
                    let sources = sources
                        .iter()
                        .map(|source| self.output_endpoint(scope, source))
                        .collect::<Result<Vec<_>, _>>()?;
                    let targets = match target {
                        ResultTarget::Leaf(id) => vec![self.scopes[scope.index()].local_node(*id)?],
                        ResultTarget::Port(port) => self.input_port(scope, port)?,
                    };
                    for target in targets {
                        self.bind_results(target, &sources)?;
                    }
                }
            }
        }
        for (name, targets) in structure.interface().inputs() {
            let mut resolved = Vec::new();
            for target in targets {
                resolved.extend(self.input_endpoint(scope, target)?);
            }
            self.scopes[scope.index()]
                .inputs
                .insert(name.clone(), resolved);
        }
        for (name, source) in structure.interface().outputs() {
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
        let ProjectedKind::Leaf(action) = &node.kind else {
            return Err(GraphError::ResultTargetNotLeaf(node.identity));
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
                ProjectedKind::Leaf(action) => action.output_type(),
                ProjectedKind::Graph(_) => NativeValueType::of::<()>(),
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
                self.nodes[target.index()].prerequisites.push(*source);
            } else {
                self.ports.push(PortConnection::new(*source, target));
            }
        }
        Ok(())
    }

    fn child_scope(&self, scope: GraphId, owner: NodeIdentity) -> Result<GraphId, GraphError> {
        let id = self.scopes[scope.index()].local_node(owner)?;
        match self.nodes[id.index()].kind {
            ProjectedKind::Graph(child) => Ok(child),
            ProjectedKind::Leaf(_) => Err(GraphError::ExpectedGraph(owner)),
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
}
