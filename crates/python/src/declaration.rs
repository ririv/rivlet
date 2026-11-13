use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::Arc;

use action::{NativeActionHandle, NativeValueType};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use rivlet::{
    ConditionId, DeclarationNode, DeclarationPrerequisite, DeclaredGraph, DeclaredNodeKind,
    GraphDeclaration, GraphId, NodeActivation, NodeId, PortConnection, RuntimeCondition,
};

use crate::NativeCompiledExecutionGraph;
use crate::mixed_execution::{NativeActionBindings, NativeActionDeclaration};
use crate::native_graph::NativeGraphStructure;
use crate::result_values::ValueCodecs;

type ExportOrigins = (
    Vec<usize>,
    Vec<(Option<Vec<usize>>, usize)>,
    Vec<(Vec<usize>, Option<usize>)>,
);

struct PythonTypes<'py> {
    graph: Bound<'py, PyAny>,
    runtime_condition: Bound<'py, PyAny>,
    input_port: Bound<'py, PyAny>,
    output_port: Bound<'py, PyAny>,
}

impl<'py> PythonTypes<'py> {
    fn import(py: Python<'py>) -> PyResult<Self> {
        let model = py.import("rivlet.model")?;
        let interface = py.import("rivlet.interface")?;
        Ok(Self {
            graph: model.getattr("Graph")?,
            runtime_condition: model.getattr("_RuntimeCondition")?,
            input_port: interface.getattr("InputPort")?,
            output_port: interface.getattr("OutputPort")?,
        })
    }
}

struct GraphRecord {
    source_graph: Py<PyAny>,
    label: String,
    label_path: Vec<String>,
    owner: Option<usize>,
    local_nodes: HashMap<usize, usize>,
    local_node_order: Vec<usize>,
    descendant_nodes: HashMap<usize, Vec<usize>>,
    port_scopes: Vec<usize>,
    ports: PortDefinitions,
}

enum PortDefinitions {
    Python,
    Rust {
        inputs: BTreeMap<String, Vec<usize>>,
        outputs: BTreeMap<String, usize>,
    },
}

struct NodeRecord {
    identity: u64,
    source_node: Option<Py<PyAny>>,
    content: NodeContent,
    graph: usize,
    node_path: Vec<usize>,
    label: String,
    inputs: Vec<usize>,
}

enum NodeContent {
    Python(Py<PyAny>),
    Native(NativeActionHandle, Arc<ValueCodecs>),
    Composite(usize),
}

impl NodeRecord {
    fn child_graph(&self) -> Option<usize> {
        match self.content {
            NodeContent::Composite(child) => Some(child),
            NodeContent::Python(_) | NodeContent::Native(_, _) => None,
        }
    }
}

struct ConditionRecord {
    predicate: Py<PyAny>,
    reads: Vec<usize>,
    target: usize,
    edge_index: Option<usize>,
}

struct Collector<'py> {
    py: Python<'py>,
    types: PythonTypes<'py>,
    graphs: Vec<GraphRecord>,
    nodes: Vec<NodeRecord>,
    declaration_nodes: Vec<DeclarationNode>,
    activations: Vec<NodeActivation>,
    conditions: Vec<ConditionRecord>,
    runtime_conditions: Vec<RuntimeCondition>,
    port_connections: Vec<PortConnection>,
    disabled_edges: Vec<(usize, usize)>,
    result_groups: HashMap<usize, usize>,
}

impl<'py> Collector<'py> {
    fn new(py: Python<'py>) -> PyResult<Self> {
        Ok(Self {
            py,
            types: PythonTypes::import(py)?,
            graphs: Vec::new(),
            nodes: Vec::new(),
            declaration_nodes: Vec::new(),
            activations: Vec::new(),
            conditions: Vec::new(),
            runtime_conditions: Vec::new(),
            port_connections: Vec::new(),
            disabled_edges: Vec::new(),
            result_groups: HashMap::new(),
        })
    }

    fn collect_root(
        mut self,
        source_graph: &Bound<'py, PyAny>,
        root_label: &str,
        root_label_path: Vec<String>,
        id_prefix: &[usize],
    ) -> PyResult<NativeDeclarationCompilation> {
        self.collect_graph(
            source_graph,
            root_label,
            root_label_path,
            None,
            id_prefix,
            true,
        )?;
        // 所有公开入口完成外层接线后，再按实际参数组校验每个活跃叶子。
        // 时间 O(V + R)，不根据函数名或参数名推断绑定。
        for (index, node) in self.nodes.iter().enumerate() {
            if node.child_graph().is_none()
                && matches!(self.node_activation(index), NodeActivation::Active { .. })
            {
                match &node.content {
                    NodeContent::Python(action) => {
                        action
                            .bind(self.py)
                            .call_method1("validate_inputs", (node.inputs.len(),))?;
                    }
                    NodeContent::Native(action, _) => self.validate_native_inputs(node, action)?,
                    NodeContent::Composite(_) => unreachable!(),
                }
            }
        }
        let graph_owners = self.graphs.iter().map(|graph| {
            graph.owner.map_or(DeclaredGraph::Root, |owner| {
                DeclaredGraph::OwnedBy(NodeId::new(owner))
            })
        });
        let compilation = GraphDeclaration::new(
            graph_owners,
            self.declaration_nodes,
            self.runtime_conditions,
            self.port_connections,
        )
        .compile()
        .map_err(|error| PyValueError::new_err(error.to_string()))?;
        let declaration_nodes = compilation
            .declaration_nodes()
            .iter()
            .map(|node| node.index())
            .collect();
        let declaration_conditions = compilation
            .declaration_conditions()
            .iter()
            .map(|condition| condition.index())
            .collect();
        let compact: HashMap<_, _> = compilation
            .declaration_nodes()
            .iter()
            .enumerate()
            .map(|(index, node)| (node.index(), index))
            .collect();
        let mut python_sources = HashSet::new();
        let native_actions = NativeActionBindings::new(
            compilation
                .declaration_nodes()
                .iter()
                .map(|node| {
                    let record = &self.nodes[node.index()];
                    match &record.content {
                        NodeContent::Python(_) | NodeContent::Composite(_) => None,
                        NodeContent::Native(action, codecs) => {
                            for source in &record.inputs {
                                if matches!(self.nodes[*source].content, NodeContent::Python(_)) {
                                    python_sources.insert(compact[source]);
                                }
                            }
                            Some(NativeActionDeclaration {
                                action: action.clone(),
                                codecs: Arc::clone(codecs),
                                inputs: record
                                    .inputs
                                    .iter()
                                    .map(|source| compact[source])
                                    .collect(),
                            })
                        }
                    }
                })
                .collect(),
            python_sources.into_iter().collect(),
        );
        let graph = NativeCompiledExecutionGraph {
            graph: Arc::new(compilation.into_graph()),
        };
        Ok(NativeDeclarationCompilation {
            graph,
            declaration_nodes,
            declaration_conditions,
            graphs: self.graphs,
            nodes: self.nodes,
            conditions: self.conditions,
            native_actions,
            disabled_edges: self.disabled_edges,
            id_prefix: id_prefix.to_vec(),
        })
    }

    fn validate_native_inputs(
        &self,
        node: &NodeRecord,
        action: &NativeActionHandle,
    ) -> PyResult<()> {
        if action.input_types().len() != node.inputs.len() {
            return Err(PyValueError::new_err(format!(
                "native action expects {} result inputs, received {}",
                action.input_types().len(),
                node.inputs.len()
            )));
        }
        for (source, expected) in node.inputs.iter().zip(action.input_types()) {
            let actual = match &self.nodes[*source].content {
                NodeContent::Native(source, _) => Some(source.output_type()),
                NodeContent::Composite(_) => Some(NativeValueType::of::<()>()),
                NodeContent::Python(_) => None,
            };
            if let Some(actual) = actual {
                if actual != *expected {
                    return Err(PyValueError::new_err(format!(
                        "native result type mismatch: expected {}, received {}",
                        expected.name(),
                        actual.name()
                    )));
                }
            }
        }
        Ok(())
    }

    fn collect_graph(
        &mut self,
        source_graph: &Bound<'py, PyAny>,
        default_label: &str,
        label_path: Vec<String>,
        owner: Option<usize>,
        id_prefix: &[usize],
        parent_active: bool,
    ) -> PyResult<usize> {
        let label = default_label.to_owned();
        let graph_index = self.graphs.len();
        let mut port_scopes = owner.map_or_else(Vec::new, |node| {
            self.graphs[self.nodes[node].graph].port_scopes.clone()
        });
        let interface = source_graph.getattr("interface")?;
        if interface.getattr("inputs")?.len()? != 0 || interface.getattr("outputs")?.len()? != 0 {
            port_scopes.push(graph_index);
        }
        self.graphs.push(GraphRecord {
            source_graph: source_graph.clone().unbind(),
            label,
            label_path,
            owner,
            local_nodes: HashMap::new(),
            local_node_order: Vec::new(),
            descendant_nodes: HashMap::new(),
            port_scopes,
            ports: PortDefinitions::Python,
        });

        let steps = source_graph.getattr("nodes")?;
        let static_bindings = source_graph.getattr("_static_bindings")?;
        // 宿主按挂载一次性求出静态选择，Rust 只消费不透明节点身份；不在
        // core 中解释宿主选择规则。C 个分支时间/空间 O(C)，普通节点查询 O(1)。
        let mut inactive_selection_steps = HashSet::new();
        if parent_active && source_graph.getattr("static_selections")?.len()? != 0 {
            for step in source_graph
                .call_method0("_resolve_static_selections")?
                .try_iter()?
            {
                inactive_selection_steps.insert(object_key(&step?));
            }
        }
        // 无静态规则时不逐节点跨语言查询；有规则时按哈希索引 O(1) 查找。
        let has_static_bindings = static_bindings.len()? != 0;
        let mut step_values = Vec::new();
        for step in steps.try_iter()? {
            step_values.push(step?);
        }
        for (local_index, step) in step_values.iter().enumerate() {
            let node_index = self.nodes.len();
            self.graphs[graph_index]
                .local_nodes
                .insert(object_key(step), node_index);
            self.graphs[graph_index].local_node_order.push(node_index);

            let mut node_path = id_prefix.to_owned();
            node_path.push(local_index);
            let label_object = step.getattr("label")?;
            let step_label = if label_object.is_none() {
                node_path
                    .iter()
                    .map(usize::to_string)
                    .collect::<Vec<_>>()
                    .join(".")
            } else {
                label_object.extract::<String>()?
            };
            let active = parent_active
                && !inactive_selection_steps.contains(&object_key(step))
                && (!has_static_bindings || Self::static_node_active(&static_bindings, step)?);
            let action = step.clone();
            self.nodes.push(NodeRecord {
                source_node: Some(step.clone().unbind()),
                identity: step.getattr("id")?.getattr("value")?.extract()?,
                content: NodeContent::Python(action.clone().unbind()),
                graph: graph_index,
                node_path: node_path.clone(),
                label: step_label.clone(),
                inputs: Vec::new(),
            });
            self.index_port_node(graph_index, object_key(step), node_index);
            let activation = if active {
                NodeActivation::Active {
                    prerequisites: Vec::new(),
                    condition: None,
                }
            } else {
                NodeActivation::Inactive
            };
            self.activations.push(activation.clone());
            self.declaration_nodes.push(DeclarationNode::new(
                GraphId::new(graph_index),
                DeclaredNodeKind::Leaf,
                activation,
            ));

            self.collect_nested_action(
                &action,
                graph_index,
                node_index,
                &node_path,
                &step_label,
                active,
            )?;
        }

        self.collect_result_inputs(source_graph, graph_index, parent_active)?;
        self.collect_node_conditions(graph_index, parent_active)?;
        self.collect_edges(source_graph, graph_index, parent_active)?;
        Ok(graph_index)
    }

    fn index_port_node(&mut self, graph: usize, identity: usize, node: usize) {
        // 仅对有端口的祖先作用域建索引；无端口图不额外建立节点索引。
        // 时间/空间 O(节点到有端口祖先的关系数)，最坏为全部路径长度之和。
        // 端口查找平均 O(1)，避免 P 个端口各自扫描 V 个节点造成 O(P*V)。
        for offset in 0..self.graphs[graph].port_scopes.len() {
            let index = self.graphs[graph].port_scopes[offset];
            self.graphs[index]
                .descendant_nodes
                .entry(identity)
                .or_default()
                .push(node);
        }
    }

    fn collect_nested_action(
        &mut self,
        action: &Bound<'py, PyAny>,
        graph_index: usize,
        node_index: usize,
        node_path: &[usize],
        step_label: &str,
        active: bool,
    ) -> PyResult<()> {
        let mut child_label_path = self.graphs[graph_index].label_path.clone();
        child_label_path.push(step_label.to_owned());
        let child_graph = if action.is_instance(&self.types.graph)? {
            let structure = action.getattr("structure")?;
            if let Ok(native_graph) = structure.extract::<PyRef<'_, NativeGraphStructure>>() {
                Some(self.collect_native_graph(
                    &native_graph,
                    &child_label_path,
                    node_index,
                    node_path,
                    active,
                )?)
            } else {
                Some(self.collect_graph(
                    &structure,
                    step_label,
                    child_label_path,
                    Some(node_index),
                    node_path,
                    active,
                )?)
            }
        } else {
            None
        };
        let Some(child_graph) = child_graph else {
            return Ok(());
        };
        self.nodes[node_index].content = NodeContent::Composite(child_graph);
        self.replace_node_kind(
            node_index,
            DeclaredNodeKind::Composite {
                child_graph: GraphId::new(child_graph),
            },
        );
        Ok(())
    }

    fn collect_native_graph(
        &mut self,
        native_graph: &NativeGraphStructure,
        label_path: &[String],
        owner: usize,
        id_prefix: &[usize],
        parent_active: bool,
    ) -> PyResult<usize> {
        let projection = native_graph.projection()?;
        let first_graph = self.graphs.len();
        let first_node = self.nodes.len();
        // V/E/P/R/L 为挂载、边、端口名、展开端点与路径总长，K 为单层端口数：
        // O(V+E+P*log(K+1)+R+L) 整批导入侧表，复用投影与 Rust action handles；
        // Graph 直接登记为 Composite，不制造空 action 或 Python 回调。
        self.collect_native_scopes(
            &projection,
            native_graph.codecs(),
            label_path,
            owner,
            first_node,
        )?;
        for node in projection.nodes() {
            let graph = first_graph + node.graph().index();
            let (content, kind) = match node.kind() {
                graph::ProjectedKind::Leaf(action) => (
                    NodeContent::Native(action.clone(), Arc::clone(native_graph.codecs())),
                    DeclaredNodeKind::Leaf,
                ),
                graph::ProjectedKind::Graph(child) => {
                    let child = first_graph + child.index();
                    (
                        NodeContent::Composite(child),
                        DeclaredNodeKind::Composite {
                            child_graph: GraphId::new(child),
                        },
                    )
                }
            };
            let mut path = id_prefix.to_vec();
            path.extend(node.path());
            self.nodes.push(NodeRecord {
                identity: node.identity().value(),
                source_node: None,
                content,
                graph,
                node_path: path,
                label: node.label().to_owned(),
                inputs: node
                    .inputs()
                    .iter()
                    .map(|id| first_node + id.index())
                    .collect(),
            });
            let activation = if parent_active {
                NodeActivation::Active {
                    prerequisites: node
                        .prerequisites()
                        .iter()
                        .map(|id| {
                            DeclarationPrerequisite::Required(NodeId::new(first_node + id.index()))
                        })
                        .collect(),
                    condition: None,
                }
            } else {
                NodeActivation::Inactive
            };
            self.activations.push(activation.clone());
            self.declaration_nodes.push(DeclarationNode::new(
                GraphId::new(graph),
                kind,
                activation,
            ));
        }
        if parent_active {
            for port in projection.port_connections() {
                self.port_connections.push(PortConnection::new(
                    NodeId::new(first_node + port.source().index()),
                    NodeId::new(first_node + port.target().index()),
                ));
            }
        }
        Ok(first_graph)
    }

    fn collect_native_scopes(
        &mut self,
        projection: &Arc<graph::Projection>,
        codecs: &Arc<ValueCodecs>,
        label_path: &[String],
        owner: usize,
        first_node: usize,
    ) -> PyResult<()> {
        for (index, scope) in projection.scopes().iter().enumerate() {
            let scope_owner = scope
                .owner()
                .map_or(owner, |node| first_node + node.index());
            let (label, path) = if let Some(local_owner) = scope.owner() {
                let node = &projection.nodes()[local_owner.index()];
                let mut path = label_path.to_vec();
                let mut ancestors = Vec::new();
                let mut current = Some(local_owner);
                while let Some(id) = current {
                    let ancestor = &projection.nodes()[id.index()];
                    ancestors.push(ancestor.label().to_owned());
                    current = projection.scopes()[ancestor.graph().index()].owner();
                }
                path.extend(ancestors.into_iter().rev());
                (node.label().to_owned(), path)
            } else {
                (self.nodes[owner].label.clone(), label_path.to_vec())
            };
            self.graphs.push(GraphRecord {
                source_graph: Py::new(
                    self.py,
                    NativeGraphStructure::view(
                        Arc::clone(projection),
                        GraphId::new(index),
                        Arc::clone(codecs),
                    ),
                )?
                .into_any(),
                label,
                label_path: path,
                owner: Some(scope_owner),
                local_nodes: HashMap::new(),
                local_node_order: scope
                    .nodes()
                    .iter()
                    .map(|id| first_node + id.index())
                    .collect(),
                descendant_nodes: HashMap::new(),
                port_scopes: Vec::new(),
                ports: PortDefinitions::Rust {
                    inputs: scope
                        .inputs()
                        .iter()
                        .map(|(name, nodes)| {
                            (
                                name.clone(),
                                nodes.iter().map(|id| first_node + id.index()).collect(),
                            )
                        })
                        .collect(),
                    outputs: scope
                        .outputs()
                        .iter()
                        .map(|(name, id)| (name.clone(), first_node + id.index()))
                        .collect(),
                },
            });
        }
        Ok(())
    }

    fn static_node_active(
        bindings: &Bound<'py, PyAny>,
        step: &Bound<'py, PyAny>,
    ) -> PyResult<bool> {
        let binding = bindings.call_method1("get", (step,))?;
        if binding.is_none() {
            return Ok(true);
        }
        binding.call_method0("evaluate")?.extract()
    }

    fn collect_node_conditions(&mut self, graph_index: usize, parent_active: bool) -> PyResult<()> {
        let node_indexes = self.graphs[graph_index].local_node_order.clone();
        for node_index in node_indexes {
            if !parent_active
                || matches!(self.node_activation(node_index), NodeActivation::Inactive)
            {
                continue;
            }
            let Some(source_node) = &self.nodes[node_index].source_node else {
                continue;
            };
            let step = source_node.bind(self.py).clone();
            let condition = step.getattr("condition")?;
            if condition.is_instance(&self.types.runtime_condition)? {
                let condition = self.register_runtime_condition(&condition, node_index, None)?;
                self.set_node_condition(node_index, condition);
            } else if !condition.is_none() {
                return Err(PyValueError::new_err("unsupported graph step condition"));
            }
        }
        Ok(())
    }

    fn collect_edges(
        &mut self,
        source_graph: &Bound<'py, PyAny>,
        graph_index: usize,
        graph_active: bool,
    ) -> PyResult<()> {
        let edges = source_graph.getattr("edges")?;
        let static_bindings = source_graph.getattr("_static_bindings")?;
        let has_static_bindings = static_bindings.len()? != 0;
        for (edge_index, edge) in edges.try_iter()?.enumerate() {
            let edge = edge?;
            if !edge.getattr("_result_index")?.is_none() {
                // 结果边的调度约束与参数绑定在同一处按真实端点处理。
                continue;
            }
            let source_node = edge.getattr("source")?;
            let target_step = edge.getattr("target")?;
            let source = self.require_local_node(graph_index, &source_node)?;
            let target = self.require_local_node(graph_index, &target_step)?;
            if !graph_active || matches!(self.node_activation(target), NodeActivation::Inactive) {
                continue;
            }
            // 结果边已在 collect_result_inputs 中求值并校验，避免重复调用求值器。
            if has_static_bindings && edge.getattr("_result_index")?.is_none() {
                let binding = static_bindings.call_method1("get", ((source_node, target_step),))?;
                if !binding.is_none() && !binding.call_method0("evaluate")?.extract::<bool>()? {
                    self.disabled_edges.push((graph_index, edge_index));
                    continue;
                }
            }
            if !edge.getattr("ports")?.is_none() {
                self.collect_port_connection(&edge, graph_index, edge_index)?;
                continue;
            }
            let condition = edge.getattr("condition")?;
            let prerequisite = if condition.is_none() {
                DeclarationPrerequisite::Required(NodeId::new(source))
            } else if condition.is_instance(&self.types.runtime_condition)? {
                DeclarationPrerequisite::RuntimeConditional {
                    node: NodeId::new(source),
                    condition: self.register_runtime_condition(
                        &condition,
                        target,
                        Some(edge_index),
                    )?,
                }
            } else {
                return Err(PyValueError::new_err("unsupported graph edge condition"));
            };
            self.push_node_prerequisite(target, prerequisite);
        }
        Ok(())
    }

    fn collect_port_connection(
        &mut self,
        edge: &Bound<'py, PyAny>,
        graph_index: usize,
        edge_index: usize,
    ) -> PyResult<()> {
        let provider_component = self.require_local_node(graph_index, &edge.getattr("source")?)?;
        let consumer_component = self.require_local_node(graph_index, &edge.getattr("target")?)?;
        let ports = edge.getattr("ports")?;
        let provider = self
            .resolve_named_output(provider_component, &ports.get_item(0)?.extract::<String>()?)?;
        let consumers = self
            .resolve_named_inputs(consumer_component, &ports.get_item(1)?.extract::<String>()?)?;
        let declared_condition = edge.getattr("condition")?;
        let condition = if declared_condition.is_none() {
            None
        } else {
            Some(self.register_runtime_condition(
                &declared_condition,
                consumer_component,
                Some(edge_index),
            )?)
        };
        // 一个公开输入可以约束多个内部步骤；不插入额外的运行时等待节点。
        for consumer in consumers {
            let source = NodeId::new(provider);
            let target = NodeId::new(consumer);
            self.port_connections.push(match condition {
                None => PortConnection::new(source, target),
                Some(condition) => PortConnection::conditional(source, target, condition),
            });
        }
        Ok(())
    }

    fn resolve_named_output(&self, component: usize, name: &str) -> PyResult<usize> {
        let graph = self.require_child_graph(component)?;
        match &self.graphs[graph].ports {
            PortDefinitions::Rust { outputs, .. } => outputs
                .get(name)
                .copied()
                .ok_or_else(|| PyValueError::new_err(format!("unknown output port: {name}"))),
            PortDefinitions::Python => {
                let name = name.into_pyobject(self.py)?;
                let definition = require_interface_port(
                    self.graphs[graph].source_graph.bind(self.py),
                    "outputs",
                    name.as_any(),
                )?;
                self.resolve_output_definition(component, &definition)
            }
        }
    }

    fn resolve_named_inputs(&self, component: usize, name: &str) -> PyResult<Vec<usize>> {
        let graph = self.require_child_graph(component)?;
        match &self.graphs[graph].ports {
            PortDefinitions::Rust { inputs, .. } => inputs
                .get(name)
                .cloned()
                .ok_or_else(|| PyValueError::new_err(format!("unknown input port: {name}"))),
            PortDefinitions::Python => {
                let name = name.into_pyobject(self.py)?;
                let definitions = require_interface_port(
                    self.graphs[graph].source_graph.bind(self.py),
                    "inputs",
                    name.as_any(),
                )?;
                let mut nodes = Vec::new();
                for definition in definitions.try_iter()? {
                    nodes.extend(self.resolve_input_definition(component, &definition?)?);
                }
                Ok(nodes)
            }
        }
    }

    fn resolve_output_definition(
        &self,
        owner: usize,
        value: &Bound<'py, PyAny>,
    ) -> PyResult<usize> {
        if value.is_instance(&self.types.output_port)? {
            let child = self.require_descendant_node(owner, &value.getattr("graph")?)?;
            self.resolve_named_output(child, &value.getattr("name")?.extract::<String>()?)
        } else {
            self.require_descendant_node(owner, value)
        }
    }

    fn resolve_input_definition(
        &self,
        owner: usize,
        value: &Bound<'py, PyAny>,
    ) -> PyResult<Vec<usize>> {
        if value.is_instance(&self.types.input_port)? {
            let child = self.require_descendant_node(owner, &value.getattr("graph")?)?;
            self.resolve_named_inputs(child, &value.getattr("name")?.extract::<String>()?)
        } else {
            Ok(vec![self.require_descendant_node(owner, value)?])
        }
    }

    fn resolve_result_source(&self, scope: usize, value: &Bound<'py, PyAny>) -> PyResult<usize> {
        if value.is_instance(&self.types.output_port)? {
            let owner = self.require_local_node(scope, &value.getattr("graph")?)?;
            self.resolve_named_output(owner, &value.getattr("name")?.extract::<String>()?)
        } else {
            self.require_local_node(scope, value)
        }
    }

    fn resolve_result_targets(
        &self,
        scope: usize,
        value: &Bound<'py, PyAny>,
    ) -> PyResult<Vec<usize>> {
        if value.is_instance(&self.types.input_port)? {
            let owner = self.require_local_node(scope, &value.getattr("graph")?)?;
            self.resolve_named_inputs(owner, &value.getattr("name")?.extract::<String>()?)
        } else {
            Ok(vec![self.require_local_node(scope, value)?])
        }
    }

    fn register_runtime_condition(
        &mut self,
        condition: &Bound<'py, PyAny>,
        target: usize,
        edge_index: Option<usize>,
    ) -> PyResult<ConditionId> {
        // I 为条件的输入引用数。按当前挂载的局部索引一次解析，时间/空间 O(I)，
        // 不从结果边反查，也不把条件输入加入 action 的无条件前置。
        let graph_index = self.nodes[target].graph;
        let graph = self.graphs[graph_index].source_graph.bind(self.py);
        let bindings = graph.getattr("_static_bindings")?;
        let target_step = self.nodes[target]
            .source_node
            .as_ref()
            .expect("Python condition must belong to a Python step")
            .bind(self.py);
        let mut reads = Vec::new();
        for source in condition.getattr("inputs")?.try_iter()? {
            let source = source?;
            let node = self.require_local_node(graph_index, &source)?;
            let binding = bindings.call_method1("get", ((&source, target_step),))?;
            if matches!(self.node_activation(node), NodeActivation::Inactive)
                || (!binding.is_none() && !binding.call_method0("evaluate")?.extract::<bool>()?)
            {
                return Err(PyValueError::new_err(
                    "condition input was removed by static conditions",
                ));
            }
            reads.push(NodeId::new(node));
        }
        let condition_id = ConditionId::new(self.conditions.len());
        self.conditions.push(ConditionRecord {
            predicate: condition.getattr("when")?.unbind(),
            reads: reads.iter().map(|node| node.index()).collect(),
            target,
            edge_index,
        });
        self.runtime_conditions.push(RuntimeCondition::new(reads));
        Ok(condition_id)
    }

    fn collect_result_inputs(
        &mut self,
        graph: &Bound<'py, PyAny>,
        graph_index: usize,
        active: bool,
    ) -> PyResult<()> {
        // O(E + I)：结果边既提供调度约束，也提供稳定的位置参数绑定。
        let bindings = graph.getattr("_static_bindings")?;
        for edge in graph.getattr("edges")?.try_iter()? {
            let edge = edge?;
            let Some(index) = edge.getattr("_result_index")?.extract::<Option<usize>>()? else {
                continue;
            };
            let source_node = edge.getattr("source")?;
            let target_step = edge.getattr("target")?;
            let source_owner = self.require_local_node(graph_index, &source_node)?;
            let target_owner = self.require_local_node(graph_index, &target_step)?;
            if !active || matches!(self.node_activation(target_owner), NodeActivation::Inactive) {
                continue;
            }
            let binding = bindings.call_method1("get", ((source_node, target_step),))?;
            if matches!(self.node_activation(source_owner), NodeActivation::Inactive)
                || (!binding.is_none() && !binding.call_method0("evaluate")?.extract::<bool>()?)
            {
                return Err(PyValueError::new_err(
                    "result input was removed by static conditions",
                ));
            }
            let source =
                self.resolve_result_source(graph_index, &edge.getattr("result_source")?)?;
            let targets =
                self.resolve_result_targets(graph_index, &edge.getattr("result_target")?)?;
            let group = object_key(&edge.getattr("_connection")?.getattr("group")?);
            if matches!(self.node_activation(source), NodeActivation::Inactive) {
                return Err(PyValueError::new_err(
                    "result input was removed by static conditions",
                ));
            }
            for target in targets {
                if matches!(self.node_activation(target), NodeActivation::Inactive) {
                    continue;
                }
                if matches!(self.nodes[target].content, NodeContent::Composite(_)) {
                    return Err(PyValueError::new_err(
                        "result target must have a callable action",
                    ));
                }
                if *self.result_groups.entry(target).or_insert(group) != group {
                    return Err(PyValueError::new_err(
                        "target action must have one result connection group",
                    ));
                }
                let inputs = &mut self.nodes[target].inputs;
                if inputs.len() <= index {
                    inputs.resize(index + 1, usize::MAX);
                }
                if inputs[index] != usize::MAX {
                    return Err(PyValueError::new_err(
                        "result connection must not be repeated",
                    ));
                }
                inputs[index] = source;
                if self.nodes[source].graph == self.nodes[target].graph {
                    self.push_node_prerequisite(
                        target,
                        DeclarationPrerequisite::Required(NodeId::new(source)),
                    );
                } else {
                    self.port_connections.push(PortConnection::new(
                        NodeId::new(source),
                        NodeId::new(target),
                    ));
                }
            }
        }
        Ok(())
    }

    fn require_local_node(&self, graph_index: usize, step: &Bound<'py, PyAny>) -> PyResult<usize> {
        self.graphs[graph_index]
            .local_nodes
            .get(&object_key(step))
            .copied()
            .ok_or_else(|| PyValueError::new_err("graph edge references a step outside its graph"))
    }

    fn require_child_graph(&self, node: usize) -> PyResult<usize> {
        self.nodes[node]
            .child_graph()
            .ok_or_else(|| PyValueError::new_err("port component must contain a graph"))
    }

    fn require_descendant_node(
        &self,
        component: usize,
        step: &Bound<'py, PyAny>,
    ) -> PyResult<usize> {
        let graph = self.require_child_graph(component)?;
        let matches = self.graphs[graph].descendant_nodes.get(&object_key(step));
        match matches.map_or(&[][..], Vec::as_slice) {
            [node] => Ok(*node),
            [] => Err(PyValueError::new_err(
                "port references a node outside its component",
            )),
            _ => Err(PyValueError::new_err(
                "port references a node mounted more than once",
            )),
        }
    }

    fn node_activation(&self, node: usize) -> &NodeActivation {
        &self.activations[node]
    }

    fn replace_node_kind(&mut self, node: usize, kind: DeclaredNodeKind) {
        let activation = self.node_activation(node).clone();
        self.activations[node] = activation.clone();
        self.declaration_nodes[node] =
            DeclarationNode::new(GraphId::new(self.nodes[node].graph), kind, activation);
    }

    fn set_node_condition(&mut self, node: usize, condition: ConditionId) {
        let NodeActivation::Active {
            prerequisites,
            condition: _,
        } = self.node_activation(node).clone()
        else {
            return;
        };
        let activation = NodeActivation::Active {
            prerequisites,
            condition: Some(condition),
        };
        self.activations[node] = activation.clone();
        self.declaration_nodes[node] = DeclarationNode::new(
            GraphId::new(self.nodes[node].graph),
            self.node_kind(node),
            activation,
        );
    }

    fn push_node_prerequisite(&mut self, node: usize, prerequisite: DeclarationPrerequisite) {
        let NodeActivation::Active {
            mut prerequisites,
            condition,
        } = self.node_activation(node).clone()
        else {
            return;
        };
        prerequisites.push(prerequisite);
        let activation = NodeActivation::Active {
            prerequisites,
            condition,
        };
        self.activations[node] = activation.clone();
        self.declaration_nodes[node] = DeclarationNode::new(
            GraphId::new(self.nodes[node].graph),
            self.node_kind(node),
            activation,
        );
    }

    fn node_kind(&self, node: usize) -> DeclaredNodeKind {
        self.nodes[node]
            .child_graph()
            .map_or(DeclaredNodeKind::Leaf, |child_graph| {
                DeclaredNodeKind::Composite {
                    child_graph: GraphId::new(child_graph),
                }
            })
    }
}

fn object_key(value: &Bound<'_, PyAny>) -> usize {
    value.as_ptr() as usize
}

fn require_interface_port<'py>(
    graph: &Bound<'py, PyAny>,
    attribute: &str,
    port: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    // 映射按能力直接查找，平均 O(1)，不再逐端口跨语言扫描。
    graph
        .getattr("interface")?
        .getattr(attribute)?
        .get_item(port)
}

/// Native compilation plus opaque Python declaration metadata.
#[pyclass(frozen, module = "rivlet._native")]
pub(crate) struct NativeDeclarationCompilation {
    pub(crate) graph: NativeCompiledExecutionGraph,
    declaration_nodes: Vec<usize>,
    declaration_conditions: Vec<usize>,
    graphs: Vec<GraphRecord>,
    nodes: Vec<NodeRecord>,
    conditions: Vec<ConditionRecord>,
    native_actions: NativeActionBindings,
    disabled_edges: Vec<(usize, usize)>,
    id_prefix: Vec<usize>,
}

impl NativeDeclarationCompilation {
    // 只导出存活节点，顺序与编译索引一致；保留声明 ID 用于原作用域引用。
    // 每个节点侧表 O(V_active)，不再为裁剪节点复制字符串、路径或 Python 引用。
    fn active_nodes(&self) -> impl Iterator<Item = &NodeRecord> {
        self.declaration_nodes
            .iter()
            .map(|&index| &self.nodes[index])
    }
}

#[pymethods]
impl NativeDeclarationCompilation {
    /// Captures compiler decisions without reevaluating any host condition.
    fn export_origins(&self) -> ExportOrigins {
        // S/C 为被移除的静态边与活跃条件数，L 为复制的挂载路径总长：
        // 时间/空间 O(S + C + L)，不另存完整声明拓扑或遍历全部边。
        (
            self.id_prefix.clone(),
            self.disabled_edges
                .iter()
                .map(|&(graph, edge)| {
                    (
                        self.graphs[graph]
                            .owner
                            .map(|node| self.nodes[node].node_path.clone()),
                        edge,
                    )
                })
                .collect(),
            self.declaration_conditions
                .iter()
                .map(|&condition| {
                    let condition = &self.conditions[condition];
                    (
                        self.nodes[condition.target].node_path.clone(),
                        condition.edge_index,
                    )
                })
                .collect(),
        )
    }

    #[getter]
    fn graph(&self) -> NativeCompiledExecutionGraph {
        self.graph.clone()
    }

    fn declaration_nodes(&self) -> Vec<usize> {
        self.declaration_nodes.clone()
    }

    fn declaration_conditions(&self) -> Vec<usize> {
        self.declaration_conditions.clone()
    }

    fn node_ids(&self) -> Vec<u64> {
        self.active_nodes().map(|node| node.identity).collect()
    }

    fn node_paths(&self) -> Vec<Vec<usize>> {
        self.active_nodes()
            .map(|node| node.node_path.clone())
            .collect()
    }

    fn node_labels(&self) -> Vec<String> {
        self.active_nodes().map(|node| node.label.clone()).collect()
    }

    fn node_graphs(&self) -> Vec<usize> {
        self.active_nodes().map(|node| node.graph).collect()
    }

    fn node_child_graphs(&self) -> Vec<Option<usize>> {
        self.active_nodes().map(NodeRecord::child_graph).collect()
    }

    fn source_steps(&self, py: Python<'_>) -> Vec<Option<Py<PyAny>>> {
        self.active_nodes()
            .map(|node| node.source_node.as_ref().map(|step| step.clone_ref(py)))
            .collect()
    }

    fn actions(&self, py: Python<'_>) -> Vec<Option<Py<PyAny>>> {
        self.active_nodes()
            .map(|node| match &node.content {
                NodeContent::Python(action) => Some(action.clone_ref(py)),
                NodeContent::Native(_, _) | NodeContent::Composite(_) => None,
            })
            .collect()
    }

    fn native_actions(&self) -> NativeActionBindings {
        self.native_actions.clone()
    }

    fn graph_labels(&self) -> Vec<String> {
        self.graphs
            .iter()
            .map(|graph| graph.label.clone())
            .collect()
    }

    fn graph_label_paths(&self) -> Vec<Vec<String>> {
        self.graphs
            .iter()
            .map(|graph| graph.label_path.clone())
            .collect()
    }

    fn graph_owners(&self) -> Vec<Option<usize>> {
        self.graphs.iter().map(|graph| graph.owner).collect()
    }

    fn graph_local_nodes(&self) -> Vec<Vec<usize>> {
        self.graphs
            .iter()
            .map(|graph| graph.local_node_order.clone())
            .collect()
    }

    fn source_graphs(&self, py: Python<'_>) -> Vec<Py<PyAny>> {
        self.graphs
            .iter()
            .map(|graph| graph.source_graph.clone_ref(py))
            .collect()
    }

    fn predicates(&self, py: Python<'_>) -> Vec<Py<PyAny>> {
        self.declaration_conditions
            .iter()
            .map(|&index| self.conditions[index].predicate.clone_ref(py))
            .collect()
    }

    fn condition_reads(&self) -> Vec<Vec<usize>> {
        self.declaration_conditions
            .iter()
            .map(|&index| self.conditions[index].reads.clone())
            .collect()
    }

    fn node_inputs(&self) -> Vec<Vec<usize>> {
        self.declaration_nodes
            .iter()
            .map(|&index| self.nodes[index].inputs.clone())
            .collect()
    }
}

#[pyfunction]
#[pyo3(signature = (source_graph, root_label, root_label_path=Vec::new(), id_prefix=Vec::new()))]
#[allow(clippy::needless_pass_by_value)]
pub(crate) fn compile_declaration(
    source_graph: &Bound<'_, PyAny>,
    root_label: &str,
    root_label_path: Vec<String>,
    id_prefix: Vec<usize>,
) -> PyResult<NativeDeclarationCompilation> {
    Collector::new(source_graph.py())?.collect_root(
        source_graph,
        root_label,
        root_label_path,
        &id_prefix,
    )
}
