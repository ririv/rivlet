//! Private graph fixtures; public producers use rivlet-python's structure exporter.

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::Arc;

use action::{ActionHandle, ProcessAction};
use graph::{Edge, Graph, GraphInterface, GraphStructure, NodeKind, PortName, Step};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use crate::native_structure::_NativeStructure;
use crate::result_values::ValueCodecs;

type ProcessDeclaration = (
    String,
    String,
    Vec<String>,
    String,
    std::collections::HashMap<String, String>,
);
type PortReference = (usize, Option<String>);
type InputDeclaration = (Option<String>, Vec<PortReference>);
type OutputDeclaration = (Option<String>, usize, Option<String>);

#[derive(FromPyObject)]
pub(crate) enum EdgeDeclaration {
    Completion((usize, usize)),
    Ports((usize, usize, Option<String>, Option<String>)),
    Results((Vec<PortReference>, PortReference)),
}

fn invalid(error: impl std::fmt::Display) -> PyErr {
    PyValueError::new_err(error.to_string())
}

/// Whole-graph importer boundary; no Python wrapper for individual Rust actions.
#[pyfunction(name = "_rust_process_graph", signature = (processes, arcs, inputs=Vec::new(), outputs=Vec::new()))]
pub(crate) fn rust_process_graph(
    py: Python<'_>,
    processes: Vec<ProcessDeclaration>,
    arcs: Vec<(usize, usize)>,
    inputs: Vec<(Option<String>, Vec<usize>)>,
    outputs: Vec<(Option<String>, usize)>,
) -> PyResult<Py<PyAny>> {
    let nodes = processes
        .into_iter()
        .map(|(label, program, arguments, cwd, environment)| {
            Step::new(
                label,
                ActionHandle::new(ProcessAction::new(
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
    _NativeStructure::new(
        GraphStructure::with_interface(nodes.into_iter().map(NodeKind::from), edges, interface)
            .map_err(invalid)?,
    )?
    .into_public(py)
}

/// Composes complete Rust graph bodies and their explicit interfaces.
#[pyfunction(name = "_compose_rust_graph")]
pub(crate) fn compose_rust_graph(
    py: Python<'_>,
    components: Vec<(String, Bound<'_, PyAny>)>,
    edges: Vec<EdgeDeclaration>,
    inputs: Vec<InputDeclaration>,
    outputs: Vec<OutputDeclaration>,
) -> PyResult<Py<PyAny>> {
    let components = components
        .into_iter()
        .map(|(label, body)| {
            Ok((
                label,
                body.getattr("_declaration")?
                    .extract::<PyRef<'_, _NativeStructure>>()?
                    .clone(),
            ))
        })
        .collect::<PyResult<Vec<_>>>()?;
    let mut codecs = ValueCodecs::default();
    for (_, body) in &components {
        codecs.combine(body.codecs())?;
    }
    let nodes = components
        .into_iter()
        .map(|(label, body)| Graph::new(label, body.graph()?).map_err(invalid))
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
                    .output(PortName::from(output))
                    .map_err(invalid)?,
                require(target)?
                    .interface()
                    .input(PortName::from(input))
                    .map_err(invalid)?,
            )),
            EdgeDeclaration::Results((sources, (target, input))) => {
                let sources = sources
                    .into_iter()
                    .map(|(index, port)| {
                        require(index)?
                            .interface()
                            .output(PortName::from(port))
                            .map_err(invalid)
                    })
                    .collect::<PyResult<Vec<_>>>()?;
                Edge::results(
                    sources,
                    require(target)?
                        .interface()
                        .input(PortName::from(input))
                        .map_err(invalid)?,
                )
                .map_err(invalid)
            }
        })
        .collect::<PyResult<Vec<_>>>()?;
    let mut interface = GraphInterface::default();
    for (name, targets) in inputs {
        let targets = targets
            .into_iter()
            .map(|(index, port)| {
                require(index)?
                    .interface()
                    .input(PortName::from(port))
                    .map_err(invalid)
            })
            .collect::<PyResult<Vec<_>>>()?;
        interface = interface.input(name, targets).map_err(invalid)?;
    }
    for (name, index, port) in outputs {
        interface = interface
            .output(
                name,
                require(index)?
                    .interface()
                    .output(PortName::from(port))
                    .map_err(invalid)?,
            )
            .map_err(invalid)?;
    }
    _NativeStructure::with_codecs(
        GraphStructure::with_interface(nodes.into_iter().map(NodeKind::from), edges, interface)
            .map_err(invalid)?,
        Arc::new(codecs),
    )?
    .into_public(py)
}
