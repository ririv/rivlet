//! Shared native declarations, regardless of which dynamic library owns their actions.
//!
//! Experimental Python/Rust mixed execution, including independently compiled extensions.

use std::sync::Arc;

use action::{ActionError, ActionHandle, Value, ValueType};
use graph::{GraphStructure, ProjectedKind, Projection};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use rivlet_bridge::{EdgeInfo, ImportedGraph, NodeKind, Snapshot};

use crate::result_values::ValueCodecs;

type ConditionDescription = (Vec<usize>, Option<usize>, Vec<Vec<usize>>);

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
    fn local(structure: Arc<GraphStructure>, codecs: Arc<ValueCodecs>) -> PyResult<Arc<Self>> {
        let projection = Arc::new(Projection::from_structure(structure).map_err(invalid)?);
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
                Arc::clone(projection.scopes()[scope].structure()),
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

/// Private storage; Python users receive rivlet.GraphStructure, never this payload.
#[pyclass(frozen, module = "rivlet._native", skip_from_py_object)]
#[derive(Clone)]
pub(crate) struct _NativeStructure {
    graph: Arc<NativeGraph>,
    scope: usize,
}

impl _NativeStructure {
    pub(crate) fn new(structure: GraphStructure) -> PyResult<Self> {
        Self::with_codecs(structure, Arc::new(ValueCodecs::default()))
    }
    pub(crate) fn with_codecs(
        structure: GraphStructure,
        codecs: Arc<ValueCodecs>,
    ) -> PyResult<Self> {
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
    pub(crate) fn graph(&self) -> PyResult<Arc<GraphStructure>> {
        match &self.graph.implementation {
            Implementation::Local(projection) => {
                Ok(Arc::clone(projection.scopes()[self.scope].structure()))
            }
            Implementation::External(_) => Err(invalid(
                "private Rust declaration composition cannot reinterpret an external graph; compose its Graph node and public ports",
            )),
        }
    }
    pub(crate) fn into_public(self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        py.import("rivlet.model")?
            .getattr("GraphStructure")?
            .call_method1("_from_declaration", (Py::new(py, self)?,))
            .map(Bound::unbind)
    }
    fn relative_path(&self, node: usize) -> Vec<usize> {
        let prefix = self.graph.snapshot.scopes[self.scope]
            .owner
            .map_or(0, |owner| self.graph.snapshot.nodes[owner].path.len());
        self.graph.snapshot.nodes[node].path[prefix..].to_vec()
    }
}

#[pymethods]
impl _NativeStructure {
    fn node_ids(&self) -> Vec<u64> {
        self.graph.snapshot.scopes[self.scope]
            .nodes
            .iter()
            .map(|node| self.graph.identity(*node))
            .collect()
    }
    fn nodes(&self) -> Vec<(u64, String, Option<Self>)> {
        self.graph.snapshot.scopes[self.scope]
            .nodes
            .iter()
            .map(|index| {
                let node = &self.graph.snapshot.nodes[*index];
                let child = match node.kind {
                    NodeKind::Step { .. } => None,
                    NodeKind::Graph { scope } => Some(Self::view(Arc::clone(&self.graph), scope)),
                };
                (self.graph.identity(*index), node.label.clone(), child)
            })
            .collect()
    }
    fn edges(&self, py: Python<'_>) -> PyResult<Vec<Py<PyAny>>> {
        self.graph.snapshot.scopes[self.scope]
            .edges
            .iter()
            .map(|edge| match edge {
                EdgeInfo::Order { source, target, .. } => (*source, *target)
                    .into_pyobject(py)
                    .map(|value| value.into_any().unbind()),
                EdgeInfo::Ports {
                    source,
                    target,
                    output,
                    input,
                    ..
                } => (*source, *target, output, input)
                    .into_pyobject(py)
                    .map(|value| value.into_any().unbind()),
                EdgeInfo::Result {
                    source,
                    target,
                    argument,
                    output,
                    input,
                } => (
                    *source,
                    *target,
                    *argument,
                    output.as_ref().map(|port| (port.name.as_deref(),)),
                    input.as_ref().map(|port| (port.name.as_deref(),)),
                )
                    .into_pyobject(py)
                    .map(|value| value.into_any().unbind()),
            })
            .collect()
    }
    fn conditions(&self) -> Vec<ConditionDescription> {
        self.graph
            .snapshot
            .conditions
            .iter()
            .filter(|condition| {
                self.graph.snapshot.nodes[condition.site.target()].graph == self.scope
            })
            .map(|condition| {
                (
                    self.relative_path(condition.site.target()),
                    condition.site.edge_index(),
                    condition
                        .inputs
                        .iter()
                        .map(|node| self.relative_path(*node))
                        .collect(),
                )
            })
            .collect()
    }
    fn input_ports(&self) -> Vec<Option<String>> {
        self.graph.snapshot.scopes[self.scope]
            .inputs
            .iter()
            .map(|(name, _)| name.clone())
            .collect()
    }
    fn output_ports(&self) -> Vec<Option<String>> {
        self.graph.snapshot.scopes[self.scope]
            .outputs
            .iter()
            .map(|(name, _)| name.clone())
            .collect()
    }
    fn has_input_port(&self, name: Option<&str>) -> bool {
        self.graph.snapshot.scopes[self.scope]
            .inputs
            .iter()
            .any(|(port, _)| port.as_deref() == name)
    }
    fn has_output_port(&self, name: Option<&str>) -> bool {
        self.graph.snapshot.scopes[self.scope]
            .outputs
            .iter()
            .any(|(port, _)| port.as_deref() == name)
    }
    fn input_paths(&self, name: Option<&str>) -> PyResult<Vec<Vec<usize>>> {
        let (_, nodes) = self.graph.snapshot.scopes[self.scope]
            .inputs
            .iter()
            .find(|(port, _)| port.as_deref() == name)
            .ok_or_else(|| invalid("unknown native input port"))?;
        Ok(nodes.iter().map(|node| self.relative_path(*node)).collect())
    }
    fn output_path(&self, name: Option<&str>) -> PyResult<Vec<usize>> {
        let (_, node) = self.graph.snapshot.scopes[self.scope]
            .outputs
            .iter()
            .find(|(port, _)| port.as_deref() == name)
            .ok_or_else(|| invalid("unknown native output port"))?;
        Ok(self.relative_path(*node))
    }
    fn input_arities(&self, name: Option<&str>) -> PyResult<Vec<usize>> {
        let (_, nodes) = self.graph.snapshot.scopes[self.scope]
            .inputs
            .iter()
            .find(|(port, _)| port.as_deref() == name)
            .ok_or_else(|| invalid("unknown native input port"))?;
        nodes
            .iter()
            .map(|node| match &self.graph.snapshot.nodes[*node].kind {
                NodeKind::Step { inputs, .. } => Ok(inputs.len()),
                NodeKind::Graph { .. } => Err(invalid("result target must be a native action")),
            })
            .collect()
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

#[pymethods]
impl _NativeCondition {
    fn as_input(slf: PyRef<'_, Self>) -> Py<Self> {
        slf.into()
    }

    // Python 属性 getter 需要实例接收方；独立根的输入已由构造器固定为空。
    #[allow(clippy::unused_self)]
    #[getter]
    fn inputs(&self, py: Python<'_>) -> Py<pyo3::types::PyTuple> {
        pyo3::types::PyTuple::empty(py).unbind()
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
                    _NativeStructure::view(Arc::clone(&native), scope).into_public(py)?,
                    id,
                    &node.label,
                    condition,
                ),
            )
            .map(Bound::unbind),
    }
}
