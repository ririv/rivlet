use std::collections::{BTreeMap, HashMap};
use std::path::PathBuf;

use action::{NativeActionHandle, ProcessAction};
use native_graph::{NativeGraphStructure as CoreNativeGraph, NativeLeaf};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use rivlet::{Arc as GraphArc, NodeId};

type ProcessDeclaration = (String, String, Vec<String>, String, HashMap<String, String>);

/// Python-visible opaque ownership of one complete Rust declaration graph.
#[pyclass(frozen, module = "rivlet._native", skip_from_py_object)]
#[derive(Clone)]
pub(crate) struct NativeGraphStructure {
    graph: CoreNativeGraph,
}

impl NativeGraphStructure {
    pub(crate) const fn graph(&self) -> &CoreNativeGraph {
        &self.graph
    }
}

#[pymethods]
impl NativeGraphStructure {
    fn node_ids(&self) -> Vec<u64> {
        self.graph
            .leaves()
            .iter()
            .map(|step| step.id().value())
            .collect()
    }

    /// Returns only labels and topology, never action commands or environments.
    fn structure(&self) -> (Vec<String>, Vec<(usize, usize)>) {
        (
            self.graph
                .leaves()
                .iter()
                .map(|step| step.label().to_owned())
                .collect(),
            self.graph
                .arcs()
                .iter()
                .map(|arc| (arc.source.index(), arc.target.index()))
                .collect(),
        )
    }
}

/// Constructs one graph from a complete process declaration in a single FFI call.
///
/// This is an adapter boundary for Rust importers and is intentionally not
/// re-exported by the public Python package.
#[pyfunction(name = "_native_process_graph")]
pub(crate) fn native_process_graph(
    processes: Vec<ProcessDeclaration>,
    arcs: Vec<(usize, usize)>,
) -> PyResult<NativeGraphStructure> {
    let steps = processes
        .into_iter()
        .map(|(label, program, arguments, cwd, environment)| {
            NativeLeaf::new(
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
        .map_err(|error| PyValueError::new_err(error.to_string()))?;
    let arcs = arcs
        .into_iter()
        .map(|(source, target)| GraphArc::new(NodeId::new(source), NodeId::new(target)));
    CoreNativeGraph::new(steps, arcs)
        .map(|graph| NativeGraphStructure { graph })
        .map_err(|error| PyValueError::new_err(error.to_string()))
}
