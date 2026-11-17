//! Shared native declarations, regardless of which dynamic library owns their actions.
//!
//! Experimental Python/Rust mixed execution, including independently compiled extensions.

use std::sync::Arc;

use action::{ActionError, ActionHandle, Value, ValueType};
use graph::{Graph, ProjectedKind, Projection};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use rivlet_bridge::{EdgeInfo, EndpointInfo, ImportedGraph, NodeKind, Snapshot};

use crate::result_values::ValueCodecs;

type EndpointDescription = (usize, Option<(Option<String>,)>);
type MountedPath = (Vec<usize>, Vec<String>);
type InterfaceDescription = (
    Vec<(Option<String>, Vec<EndpointDescription>)>,
    Vec<(Option<String>, EndpointDescription)>,
);

fn endpoint_description(endpoint: &EndpointInfo) -> EndpointDescription {
    match endpoint {
        EndpointInfo::Node { node } => (*node, None),
        EndpointInfo::Port { owner, name } => (*owner, Some((name.clone(),))),
    }
}

fn invalid(error: impl std::fmt::Display) -> PyErr {
    PyValueError::new_err(error.to_string())
}

enum Implementation {
    Local(Arc<Projection>),
    External(Arc<ImportedGraph>),
}

pub(crate) struct NativeGraph {
    pub(crate) snapshot: Arc<Snapshot>,
    pub(crate) codecs: Arc<ValueCodecs>,
    implementation: Implementation,
}

impl NativeGraph {
    fn local(structure: Arc<Graph>, codecs: Arc<ValueCodecs>) -> PyResult<Arc<Self>> {
        let projection = Arc::new(Projection::from_graph_contents(structure).map_err(invalid)?);
        let snapshot = Arc::new(Snapshot::from_projection(&projection).map_err(invalid)?);
        Ok(Arc::new(Self {
            snapshot,
            codecs,
            implementation: Implementation::Local(projection),
        }))
    }
    fn external(graph: Arc<ImportedGraph>) -> Arc<Self> {
        Arc::new(Self {
            snapshot: graph.shared_snapshot(),
            codecs: Arc::new(ValueCodecs::default()),
            implementation: Implementation::External(graph),
        })
    }
    pub(crate) fn identity(&self, index: usize) -> u64 {
        match &self.implementation {
            Implementation::Local(_) => self.snapshot.nodes[index].identity,
            Implementation::External(graph) => graph.identity(index).value(),
        }
    }
    pub(crate) fn action(self: &Arc<Self>, index: usize) -> PyResult<ActionHandle> {
        match &self.implementation {
            Implementation::Local(projection) => match projection.nodes()[index].kind() {
                ProjectedKind::Step(action) => Ok(action.clone()),
                ProjectedKind::Graph(_) => Err(invalid("native graph is not an action")),
            },
            Implementation::External(graph) => graph.action(index).map_err(invalid),
        }
    }
    pub(crate) fn predicate(self: &Arc<Self>, index: usize) -> NativePredicate {
        let types = match &self.implementation {
            Implementation::Local(projection) => {
                projection.conditions()[index].input_types().to_vec()
            }
            Implementation::External(graph) => self.snapshot.conditions[index]
                .types
                .iter()
                .map(|kind| rivlet_bridge::value_type(kind, graph.provider()))
                .collect(),
        };
        NativePredicate {
            graph: Arc::clone(self),
            index,
            types,
        }
    }
    fn project(self: &Arc<Self>, scope: usize) -> PyResult<Arc<Self>> {
        if scope == 0 {
            return Ok(Arc::clone(self));
        }
        match &self.implementation {
            Implementation::Local(projection) => Self::local(
                Arc::clone(projection.scopes()[scope].declaration()),
                Arc::clone(&self.codecs),
            ),
            Implementation::External(graph) => {
                graph.project(scope).map(Self::external).map_err(invalid)
            }
        }
    }
}

#[derive(Clone)]
pub(crate) struct NativePredicate {
    graph: Arc<NativeGraph>,
    index: usize,
    types: Vec<ValueType>,
}
impl NativePredicate {
    pub(crate) fn input_types(&self) -> &[ValueType] {
        &self.types
    }
    pub(crate) fn evaluate(&self, values: &[Option<Value>]) -> Result<bool, ActionError> {
        match &self.graph.implementation {
            Implementation::Local(projection) => {
                projection.conditions()[self.index].evaluate(values)
            }
            Implementation::External(graph) => graph.evaluate(self.index, values),
        }
    }
}

/// Private storage; Python users receive rivlet.Graph, never this payload.
#[pyclass(frozen, module = "rivlet._native", skip_from_py_object)]
#[derive(Clone)]
pub(crate) struct _NativeGraph {
    graph: Arc<NativeGraph>,
    scope: usize,
}

impl _NativeGraph {
    pub(crate) fn new(structure: Graph) -> PyResult<Self> {
        Self::with_codecs(structure, Arc::new(ValueCodecs::default()))
    }
    pub(crate) fn with_codecs(structure: Graph, codecs: Arc<ValueCodecs>) -> PyResult<Self> {
        Ok(Self {
            graph: NativeGraph::local(Arc::new(structure), codecs)?,
            scope: 0,
        })
    }
    pub(crate) fn view(graph: Arc<NativeGraph>, scope: usize) -> Self {
        Self { graph, scope }
    }
    pub(crate) fn for_import(&self) -> PyResult<Arc<NativeGraph>> {
        self.graph.project(self.scope)
    }
    pub(crate) fn codecs(&self) -> &Arc<ValueCodecs> {
        &self.graph.codecs
    }
    pub(crate) fn graph(&self) -> PyResult<Arc<Graph>> {
        match &self.graph.implementation {
            Implementation::Local(projection) => {
                Ok(Arc::clone(projection.scopes()[self.scope].declaration()))
            }
            Implementation::External(_) => Err(invalid(
                "private Rust declaration composition cannot reinterpret an external graph; compose its Graph node and public ports",
            )),
        }
    }
    pub(crate) fn into_public(self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let (identity, label) = if let Some(index) = self.graph.snapshot.scopes[self.scope].owner {
            let node = &self.graph.snapshot.nodes[index];
            (self.graph.identity(index), node.label.clone())
        } else {
            let declaration = self.graph()?;
            (declaration.id().value(), declaration.label().to_owned())
        };
        // 真实根条件由 Python 视图按需读取；创建编译作用域不展开其同级来源。
        py.import("rivlet.model")?
            .getattr("Graph")?
            .call_method1(
                "_from_native",
                (
                    Py::new(py, self)?,
                    identity,
                    label,
                    None::<_NativeCondition>,
                ),
            )
            .map(Bound::unbind)
    }
}

#[pymethods]
impl _NativeGraph {
    fn mounted_paths(&self, indexes: Vec<usize>) -> PyResult<Vec<Option<MountedPath>>> {
        let prefix = self.graph.snapshot.scopes[self.scope]
            .owner
            .map_or(&[][..], |owner| {
                self.graph.snapshot.nodes[owner].path.as_slice()
            });
        indexes
            .into_iter()
            .map(|index| {
                let node = self
                    .graph
                    .snapshot
                    .nodes
                    .get(index)
                    .ok_or_else(|| invalid("unknown native node"))?;
                if node.path.len() <= prefix.len() || !node.path.starts_with(prefix) {
                    return Ok(None);
                }
                let path = node.path[prefix.len()..].to_vec();
                let mut labels = Vec::with_capacity(path.len());
                let mut current = Some(index);
                while let Some(index) = current {
                    let node = &self.graph.snapshot.nodes[index];
                    if node.path.len() <= prefix.len() {
                        break;
                    }
                    labels.push(node.label.clone());
                    current = self.graph.snapshot.scopes[node.graph].owner;
                }
                labels.reverse();
                Ok(Some((path, labels)))
            })
            .collect()
    }
    fn owner_index(&self) -> Option<usize> {
        self.graph.snapshot.scopes[self.scope].owner
    }
    fn node_indexes(&self) -> Vec<usize> {
        self.graph.snapshot.scopes[self.scope].nodes.clone()
    }
    fn descendants(&self, indexes: Vec<usize>) -> PyResult<Vec<bool>> {
        let prefix = self.graph.snapshot.scopes[self.scope]
            .owner
            .map_or(&[][..], |owner| {
                self.graph.snapshot.nodes[owner].path.as_slice()
            });
        indexes
            .into_iter()
            .map(|index| {
                let node = self
                    .graph
                    .snapshot
                    .nodes
                    .get(index)
                    .ok_or_else(|| invalid("unknown native node"))?;
                Ok(node.path.len() > prefix.len() && node.path.starts_with(prefix))
            })
            .collect()
    }
    fn node_condition(&self, index: usize) -> PyResult<Option<usize>> {
        Ok(self
            .graph
            .snapshot
            .nodes
            .get(index)
            .ok_or_else(|| invalid("unknown native node"))?
            .condition)
    }
    fn node_descriptor(
        &self,
        py: Python<'_>,
        index: usize,
    ) -> PyResult<(u64, String, Py<PyAny>, Option<usize>)> {
        let node = self
            .graph
            .snapshot
            .nodes
            .get(index)
            .ok_or_else(|| invalid("unknown native node"))?;
        let body = match node.kind {
            NodeKind::Step { .. } => {
                Py::new(py, _NativeStep::new(Arc::clone(&self.graph), index))?.into_any()
            }
            NodeKind::Graph { scope } => {
                Py::new(py, Self::view(Arc::clone(&self.graph), scope))?.into_any()
            }
        };
        Ok((
            self.graph.identity(index),
            node.label.clone(),
            body,
            node.condition,
        ))
    }
    fn condition_descriptor(
        &self,
        index: usize,
    ) -> PyResult<(_NativeCondition, Vec<EndpointDescription>)> {
        let info = self
            .graph
            .snapshot
            .conditions
            .get(index)
            .ok_or_else(|| invalid("unknown native predicate"))?;
        Ok((
            _NativeCondition::bound(&self.graph, index),
            info.sources.iter().map(endpoint_description).collect(),
        ))
    }
    fn interface_descriptor(&self) -> InterfaceDescription {
        let interface = &self.graph.snapshot.scopes[self.scope].interface;
        (
            interface
                .inputs
                .iter()
                .map(|(name, endpoints)| {
                    (
                        name.clone(),
                        endpoints.iter().map(endpoint_description).collect(),
                    )
                })
                .collect(),
            interface
                .outputs
                .iter()
                .map(|(name, endpoint)| (name.clone(), endpoint_description(endpoint)))
                .collect(),
        )
    }
    fn edge_descriptors(&self, py: Python<'_>) -> PyResult<Vec<Py<PyAny>>> {
        let scope = &self.graph.snapshot.scopes[self.scope];
        let mut edges = Vec::new();
        let mut index = 0;
        while index < scope.edges.len() {
            match &scope.edges[index] {
                EdgeInfo::Order {
                    source,
                    target,
                    condition,
                } => {
                    edges.push(
                        (0_u8, *source, *target, *condition)
                            .into_pyobject(py)?
                            .into_any()
                            .unbind(),
                    );
                    index += 1;
                }
                EdgeInfo::Ports {
                    source,
                    target,
                    output,
                    input,
                    condition,
                } => {
                    edges.push(
                        (
                            1_u8,
                            *source,
                            *target,
                            output.clone(),
                            input.clone(),
                            *condition,
                        )
                            .into_pyobject(py)?
                            .into_any()
                            .unbind(),
                    );
                    index += 1;
                }
                EdgeInfo::Result { target, input, .. } => {
                    let receiver = (
                        scope.nodes[*target],
                        input.as_ref().map(|port| (port.name.clone(),)),
                    );
                    let mut sources = Vec::new();
                    while let Some(EdgeInfo::Result {
                        source,
                        target,
                        argument,
                        output,
                        input,
                    }) = scope.edges.get(index)
                    {
                        if *argument == 0 && !sources.is_empty() {
                            break;
                        }
                        if *argument != sources.len()
                            || receiver
                                != (
                                    scope.nodes[*target],
                                    input.as_ref().map(|port| (port.name.clone(),)),
                                )
                        {
                            return Err(invalid("invalid native result argument group"));
                        }
                        sources.push((
                            scope.nodes[*source],
                            output.as_ref().map(|port| (port.name.clone(),)),
                        ));
                        index += 1;
                    }
                    edges.push(
                        (2_u8, sources, receiver)
                            .into_pyobject(py)?
                            .into_any()
                            .unbind(),
                    );
                }
            }
        }
        Ok(edges)
    }
}

/// A native action binding, not a Python callable or a second public node type.
#[pyclass(frozen, module = "rivlet._native")]
pub(crate) struct _NativeStep {
    graph: Arc<NativeGraph>,
    index: usize,
}

impl _NativeStep {
    fn new(graph: Arc<NativeGraph>, index: usize) -> Self {
        Self { graph, index }
    }

    pub(crate) fn action(&self) -> PyResult<ActionHandle> {
        self.graph.action(self.index)
    }
    pub(crate) fn codecs(&self) -> Arc<ValueCodecs> {
        Arc::clone(&self.graph.codecs)
    }
}

#[pymethods]
impl _NativeStep {
    fn input_count(&self) -> PyResult<usize> {
        Ok(self.action()?.input_types().len())
    }
}

/// A root predicate retained in its origin library, without a fake Python callable.
#[pyclass(frozen, module = "rivlet._native")]
pub(crate) struct _NativeCondition {
    pub(crate) predicate: NativePredicate,
    pub(crate) codecs: Arc<ValueCodecs>,
}

impl _NativeCondition {
    fn bound(graph: &Arc<NativeGraph>, index: usize) -> Self {
        Self {
            predicate: graph.predicate(index),
            codecs: Arc::clone(&graph.codecs),
        }
    }
    fn new(graph: &Arc<NativeGraph>, index: usize) -> PyResult<Self> {
        let predicate = graph.predicate(index);
        // 独立根节点没有外部同级声明；不能静默丢弃条件输入。
        if !predicate.input_types().is_empty() {
            return Err(invalid(
                "exported root predicate references an external node",
            ));
        }
        Ok(Self {
            predicate,
            codecs: Arc::clone(&graph.codecs),
        })
    }
}

pub(crate) fn import_node(py: Python<'_>, graph: Arc<ImportedGraph>) -> PyResult<Py<PyAny>> {
    let native = NativeGraph::external(graph);
    let scope = &native.snapshot.scopes[0];
    let [index] = scope.nodes.as_slice() else {
        return Err(invalid(
            "native node export must contain exactly one root node",
        ));
    };
    let node = &native.snapshot.nodes[*index];
    let condition = node
        .condition
        .map(|index| _NativeCondition::new(&native, index))
        .transpose()?;
    let model = py.import("rivlet.model")?;
    let id = native.identity(*index);
    match node.kind {
        NodeKind::Step { .. } => model
            .getattr("Step")?
            .call_method1(
                "_from_native",
                (
                    Py::new(py, _NativeStep::new(Arc::clone(&native), *index))?,
                    id,
                    &node.label,
                    condition,
                ),
            )
            .map(Bound::unbind),
        NodeKind::Graph { scope } => model
            .getattr("Graph")?
            .call_method1(
                "_from_native",
                (
                    Py::new(py, _NativeGraph::view(Arc::clone(&native), scope))?,
                    id,
                    &node.label,
                    condition,
                ),
            )
            .map(Bound::unbind),
    }
}
