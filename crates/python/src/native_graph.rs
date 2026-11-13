use std::collections::{BTreeMap, HashMap};
use std::path::PathBuf;
use std::sync::Arc;

use action::{NativeActionHandle, ProcessAction};
use graph::{Edge, Graph, GraphInterface, GraphStructure, Leaf, Node, NodeKind, Projection};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use rivlet::GraphId;

type ProcessDeclaration = (String, String, Vec<String>, String, HashMap<String, String>);
type NodeMetadata = (u64, String, Option<NativeGraphStructure>);
type InputDeclaration = (String, Vec<(usize, String)>);
type OutputDeclaration = (String, usize, String);

#[derive(FromPyObject)]
pub(crate) enum EdgeDeclaration {
    Completion((usize, usize)),
    Ports((usize, usize, String, String)),
}

fn invalid(error: impl std::fmt::Display) -> PyErr {
    PyValueError::new_err(error.to_string())
}

/// Opaque ownership of a complete Rust declaration, or a shared nested view.
#[pyclass(frozen, module = "rivlet._native", skip_from_py_object)]
#[derive(Clone)]
pub(crate) struct NativeGraphStructure {
    projection: Arc<Projection>,
    scope: GraphId,
}

impl NativeGraphStructure {
    fn new(structure: GraphStructure) -> PyResult<Self> {
        Ok(Self {
            projection: Arc::new(Projection::from_structure(Arc::new(structure)).map_err(invalid)?),
            scope: GraphId::new(0),
        })
    }

    pub(crate) fn graph(&self) -> &Arc<GraphStructure> {
        self.projection.scopes()[self.scope.index()].structure()
    }

    pub(crate) fn projection(&self) -> PyResult<Arc<Projection>> {
        if self.scope.index() == 0 {
            Ok(Arc::clone(&self.projection))
        } else {
            // 仅当用户把嵌套视图作为新的根挂载时重新定位；普通遍历共享一次投影。
            Ok(Arc::new(
                Projection::from_structure(Arc::clone(self.graph())).map_err(invalid)?,
            ))
        }
    }

    pub(crate) fn view(projection: Arc<Projection>, scope: GraphId) -> Self {
        Self { projection, scope }
    }

    fn relative_path(&self, node: rivlet::NodeId) -> Vec<usize> {
        let scope = &self.projection.scopes()[self.scope.index()];
        let prefix = scope.owner().map_or(0, |owner| {
            self.projection.nodes()[owner.index()].path().len()
        });
        self.projection.nodes()[node.index()].path()[prefix..].to_vec()
    }
}

#[pymethods]
impl NativeGraphStructure {
    fn node_ids(&self) -> Vec<u64> {
        self.graph()
            .nodes()
            .iter()
            .map(|node| node.id().value())
            .collect()
    }

    /// Returns metadata and shared child views, never commands or environments.
    fn nodes(&self) -> Vec<NodeMetadata> {
        let scope = &self.projection.scopes()[self.scope.index()];
        scope
            .nodes()
            .iter()
            .map(|id| {
                let node = &self.projection.nodes()[id.index()];
                let child = match node.kind() {
                    graph::ProjectedKind::Leaf(_) => None,
                    graph::ProjectedKind::Graph(scope) => {
                        Some(Self::view(Arc::clone(&self.projection), *scope))
                    }
                };
                (node.identity().value(), node.label().to_owned(), child)
            })
            .collect()
    }

    fn edges(&self, py: Python<'_>) -> PyResult<Vec<Py<PyAny>>> {
        let indexes: HashMap<_, _> = self
            .graph()
            .nodes()
            .iter()
            .enumerate()
            .map(|(index, node)| (node.id(), index))
            .collect();
        self.graph()
            .edges()
            .iter()
            .map(|edge| {
                let source = indexes[&edge.source()];
                let target = indexes[&edge.target()];
                match edge {
                    Edge::Completion { .. } => {
                        Ok((source, target).into_pyobject(py)?.into_any().unbind())
                    }
                    Edge::Ports {
                        source: output,
                        target: input,
                    } => Ok((source, target, output.name(), input.name())
                        .into_pyobject(py)?
                        .into_any()
                        .unbind()),
                }
            })
            .collect()
    }

    fn input_ports(&self) -> Vec<String> {
        self.graph().interface().inputs().keys().cloned().collect()
    }
    fn has_input_port(&self, name: &str) -> bool {
        self.graph().interface().inputs().contains_key(name)
    }
    fn has_output_port(&self, name: &str) -> bool {
        self.graph().interface().outputs().contains_key(name)
    }
    fn output_ports(&self) -> Vec<String> {
        self.graph().interface().outputs().keys().cloned().collect()
    }
    fn input_paths(&self, name: &str) -> PyResult<Vec<Vec<usize>>> {
        self.projection.scopes()[self.scope.index()]
            .inputs()
            .get(name)
            .map(|nodes| nodes.iter().map(|node| self.relative_path(*node)).collect())
            .ok_or_else(|| invalid(format!("unknown input port: {name}")))
    }
    fn output_path(&self, name: &str) -> PyResult<Vec<usize>> {
        self.projection.scopes()[self.scope.index()]
            .outputs()
            .get(name)
            .map(|node| self.relative_path(*node))
            .ok_or_else(|| invalid(format!("unknown output port: {name}")))
    }
}

/// Whole-graph importer boundary; no Python wrapper for individual Rust actions.
#[pyfunction(name = "_native_process_graph", signature = (processes, arcs, inputs=Vec::new(), outputs=Vec::new()))]
pub(crate) fn native_process_graph(
    processes: Vec<ProcessDeclaration>,
    arcs: Vec<(usize, usize)>,
    inputs: Vec<(String, Vec<usize>)>,
    outputs: Vec<(String, usize)>,
) -> PyResult<NativeGraphStructure> {
    let nodes = processes
        .into_iter()
        .map(|(label, program, arguments, cwd, environment)| {
            Leaf::new(
                label,
                NativeActionHandle::new(ProcessAction::new(
                    program,
                    arguments,
                    PathBuf::from(cwd),
                    environment.into_iter().collect::<BTreeMap<_, _>>(),
                )),
            )
        })
        .collect::<Result<Vec<_>, _>>()
        .map_err(invalid)?;
    let require = |index: usize| {
        nodes
            .get(index)
            .ok_or_else(|| invalid("node index is outside the graph"))
    };
    let edges = arcs
        .into_iter()
        .map(|(source, target)| Ok(Edge::new(require(source)?, require(target)?)))
        .collect::<PyResult<Vec<_>>>()?;
    let mut interface = GraphInterface::default();
    for (name, targets) in inputs {
        let targets = targets
            .into_iter()
            .map(require)
            .collect::<PyResult<Vec<_>>>()?;
        interface = interface.input(name, targets).map_err(invalid)?;
    }
    for (name, index) in outputs {
        interface = interface.output(name, require(index)?).map_err(invalid)?;
    }
    NativeGraphStructure::new(
        GraphStructure::with_interface(nodes.into_iter().map(NodeKind::from), edges, interface)
            .map_err(invalid)?,
    )
}

/// Composes complete Rust graph bodies and their explicitly named interfaces.
#[pyfunction(name = "_native_compose_graph")]
pub(crate) fn native_compose_graph(
    components: Vec<(String, PyRef<'_, NativeGraphStructure>)>,
    edges: Vec<EdgeDeclaration>,
    inputs: Vec<InputDeclaration>,
    outputs: Vec<OutputDeclaration>,
) -> PyResult<NativeGraphStructure> {
    let nodes = components
        .into_iter()
        .map(|(label, body)| Graph::new(label, Arc::clone(body.graph())).map_err(invalid))
        .collect::<PyResult<Vec<_>>>()?;
    let require = |index: usize| {
        nodes
            .get(index)
            .ok_or_else(|| invalid("component index is outside the graph"))
    };
    let edges = edges
        .into_iter()
        .map(|edge| match edge {
            EdgeDeclaration::Completion((source, target)) => {
                Ok(Edge::new(require(source)?, require(target)?))
            }
            EdgeDeclaration::Ports((source, target, output, input)) => Ok(Edge::ports(
                require(source)?
                    .interface()
                    .output(&output)
                    .map_err(invalid)?,
                require(target)?
                    .interface()
                    .input(&input)
                    .map_err(invalid)?,
            )),
        })
        .collect::<PyResult<Vec<_>>>()?;
    let mut interface = GraphInterface::default();
    for (name, targets) in inputs {
        let targets = targets
            .into_iter()
            .map(|(index, port)| require(index)?.interface().input(&port).map_err(invalid))
            .collect::<PyResult<Vec<_>>>()?;
        interface = interface.input(name, targets).map_err(invalid)?;
    }
    for (name, index, port) in outputs {
        interface = interface
            .output(
                name,
                require(index)?.interface().output(&port).map_err(invalid)?,
            )
            .map_err(invalid)?;
    }
    NativeGraphStructure::new(
        GraphStructure::with_interface(nodes.into_iter().map(NodeKind::from), edges, interface)
            .map_err(invalid)?,
    )
}
