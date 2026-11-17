//! An independent extension: only the versioned C ABI connects it to rivlet._native.
//! Experimental mixed execution and SDK; not a stable extension contract.

use std::sync::Arc;

use action::{Action, ActionContext, ActionError, ActionHandle};
use graph::{Condition, Edge, Graph, GraphError, GraphInterface, PortName, Step};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use rivlet_python::export_node;

fn invalid(error: GraphError) -> PyErr {
    PyValueError::new_err(error.to_string())
}

struct Echo;
impl Action for Echo {
    type Inputs = (Option<Arc<i64>>,);
    type Output = i64;
    fn execute(&self, _: &ActionContext, (value,): Self::Inputs) -> Result<i64, ActionError> {
        value
            .map(|value| *value)
            .ok_or_else(|| ActionError::new("value is missing"))
    }
}
struct Double;
impl Action for Double {
    type Inputs = (Option<Arc<i64>>,);
    type Output = i64;
    fn execute(&self, _: &ActionContext, (value,): Self::Inputs) -> Result<i64, ActionError> {
        value
            .map(|value| *value * 2)
            .ok_or_else(|| ActionError::new("value is missing"))
    }
}

#[pyfunction]
fn make_graph(py: Python<'_>) -> PyResult<Py<PyAny>> {
    let read = Step::new("read", ActionHandle::new(Echo)).map_err(invalid)?;
    let condition = Condition::new(
        |(value,): (Option<Arc<i64>>,)| Ok(value.is_some_and(|value| *value > 0)),
        [(&read).into()],
    )
    .map_err(invalid)?;
    let apply =
        Step::with_condition("double", ActionHandle::new(Double), condition).map_err(invalid)?;
    let interface = GraphInterface::default()
        .input(PortName::Anonymous, [&read])
        .map_err(invalid)?
        .output(PortName::Anonymous, &apply)
        .map_err(invalid)?
        .output("read", &read)
        .map_err(invalid)?;
    let body = Graph::with_interface(
        "scope",
        [read.clone().into(), apply.clone().into()],
        [Edge::results([&read], &apply).map_err(invalid)?],
        interface,
    )
    .map_err(invalid)?;
    export_node(
        py,
        Graph::with_interface(
            "rust",
            body.nodes().iter().cloned(),
            body.edges().iter().cloned(),
            body.interface().declaration().clone(),
        )
        .map_err(invalid)?,
    )
}

struct Payload(Vec<u8>);
struct Produce;
impl Action for Produce {
    type Inputs = ();
    type Output = Payload;
    fn execute(&self, _: &ActionContext, (): ()) -> Result<Payload, ActionError> {
        Ok(Payload(vec![7; 42]))
    }
}
struct SharedLength;
impl Action for SharedLength {
    type Inputs = (Option<Arc<Payload>>, Option<Arc<Payload>>);
    type Output = u64;
    fn execute(&self, _: &ActionContext, (left, right): Self::Inputs) -> Result<u64, ActionError> {
        let left = left.ok_or_else(|| ActionError::new("left payload missing"))?;
        let right = right.ok_or_else(|| ActionError::new("right payload missing"))?;
        if !Arc::ptr_eq(&left, &right) {
            return Err(ActionError::new("payload was copied instead of shared"));
        }
        u64::try_from(left.0.len()).map_err(|error| ActionError::new(error.to_string()))
    }
}

#[pyfunction]
fn make_payload_pair(py: Python<'_>) -> PyResult<(Py<PyAny>, Py<PyAny>)> {
    let produce = Step::new("produce", ActionHandle::new(Produce)).map_err(invalid)?;
    let length = Step::new("shared-length", ActionHandle::new(SharedLength)).map_err(invalid)?;
    let source = Graph::with_interface(
        "scope",
        [produce.clone().into()],
        [],
        GraphInterface::default()
            .output(PortName::Anonymous, &produce)
            .map_err(invalid)?,
    )
    .map_err(invalid)?;
    let target = Graph::with_interface(
        "scope",
        [length.clone().into()],
        [],
        GraphInterface::default()
            .input(PortName::Anonymous, [&length])
            .map_err(invalid)?
            .output(PortName::Anonymous, &length)
            .map_err(invalid)?,
    )
    .map_err(invalid)?;
    Ok((
        export_node(
            py,
            Graph::with_interface(
                "producer",
                source.nodes().iter().cloned(),
                source.edges().iter().cloned(),
                source.interface().declaration().clone(),
            )
            .map_err(invalid)?,
        )?,
        export_node(
            py,
            Graph::with_interface(
                "consumer",
                target.nodes().iter().cloned(),
                target.edges().iter().cloned(),
                target.interface().declaration().clone(),
            )
            .map_err(invalid)?,
        )?,
    ))
}

struct Broken;
impl Action for Broken {
    type Inputs = ();
    type Output = ();
    fn execute(&self, _: &ActionContext, (): ()) -> Result<(), ActionError> {
        Err(ActionError::new("example action failed"))
    }
}
struct Noop;
impl Action for Noop {
    type Inputs = ();
    type Output = ();
    fn execute(&self, _: &ActionContext, (): ()) -> Result<(), ActionError> {
        Ok(())
    }
}

#[pyfunction]
fn make_failures(py: Python<'_>) -> PyResult<Py<PyAny>> {
    let action = Step::new("failed-action", ActionHandle::new(Broken)).map_err(invalid)?;
    let predicate = Step::with_condition(
        "failed-predicate",
        ActionHandle::new(Noop),
        Condition::new(|()| Err(ActionError::new("example predicate failed")), [])
            .map_err(invalid)?,
    )
    .map_err(invalid)?;
    export_node(
        py,
        Graph::new("native", [action.into(), predicate.into()], []).map_err(invalid)?,
    )
}

#[pyfunction]
fn make_nested(py: Python<'_>) -> PyResult<Py<PyAny>> {
    let task = Step::new("task", ActionHandle::new(Noop)).map_err(invalid)?;
    let body = Arc::new(
        Graph::with_interface(
            "scope",
            [task.clone().into()],
            [],
            GraphInterface::default()
                .output(PortName::Anonymous, &task)
                .map_err(invalid)?,
        )
        .map_err(invalid)?,
    );
    let left = Graph::with_interface(
        "left",
        body.nodes().iter().cloned(),
        body.edges().iter().cloned(),
        body.interface().declaration().clone(),
    )
    .map_err(invalid)?;
    let right = Graph::with_interface(
        "right",
        body.nodes().iter().cloned(),
        body.edges().iter().cloned(),
        body.interface().declaration().clone(),
    )
    .map_err(invalid)?;
    let interface = GraphInterface::default()
        .output(
            PortName::Anonymous,
            right
                .interface()
                .output(PortName::Anonymous)
                .map_err(invalid)?,
        )
        .map_err(invalid)?;
    export_node(
        py,
        Graph::with_interface(
            "nested",
            [left.clone().into(), right.clone().into()],
            [Edge::new(&left, &right)],
            interface,
        )
        .map_err(invalid)?,
    )
}

#[pymodule]
fn demo_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(make_graph, module)?)?;
    module.add_function(wrap_pyfunction!(make_payload_pair, module)?)?;
    module.add_function(wrap_pyfunction!(make_failures, module)?)?;
    module.add_function(wrap_pyfunction!(make_nested, module)?)?;
    module.add_function(wrap_pyfunction!(make_conditions, module)?)?;
    module.add_function(wrap_pyfunction!(make_port_conditions, module)?)?;
    module.add_function(wrap_pyfunction!(make_conditional_step, module)?)?;
    module.add_function(wrap_pyfunction!(make_conditional_graph, module)?)?;
    module.add(
        "double",
        export_node(
            module.py(),
            Step::new("double", ActionHandle::new(Double)).map_err(invalid)?,
        )?,
    )?;
    module.add("pipeline", make_graph(module.py())?)?;
    Ok(())
}

struct Add;
impl Action for Add {
    type Inputs = (Option<Arc<i64>>, Option<Arc<i64>>);
    type Output = i64;
    fn execute(&self, _: &ActionContext, (left, right): Self::Inputs) -> Result<i64, ActionError> {
        Ok(*left.ok_or_else(|| ActionError::new("left missing"))?
            + *right.ok_or_else(|| ActionError::new("right missing"))?)
    }
}

#[pyfunction]
fn make_conditions(py: Python<'_>) -> PyResult<Py<PyAny>> {
    let read = Step::new("read", ActionHandle::new(Echo)).map_err(invalid)?;
    let double = Step::new("double", ActionHandle::new(Double)).map_err(invalid)?;
    let add = Step::new("add", ActionHandle::new(Add)).map_err(invalid)?;
    let wait = Step::new("wait", ActionHandle::new(Noop)).map_err(invalid)?;
    let condition = Condition::new(
        |(value,): (Option<Arc<i64>>,)| Ok(value.is_some_and(|value| *value > 0)),
        [(&read).into()],
    )
    .map_err(invalid)?;
    let task = Step::new("task", ActionHandle::new(Noop)).map_err(invalid)?;
    let body = Arc::new(
        Graph::with_interface(
            "scope",
            [task.clone().into()],
            [],
            GraphInterface::default()
                .input(PortName::Anonymous, [&task])
                .map_err(invalid)?
                .output(PortName::Anonymous, &task)
                .map_err(invalid)?,
        )
        .map_err(invalid)?,
    );
    let gated = Graph::with_condition(
        "gated",
        body.nodes().iter().cloned(),
        body.edges().iter().cloned(),
        body.interface().declaration().clone(),
        condition.clone(),
    )
    .map_err(invalid)?;
    let target = Graph::with_interface(
        "target",
        body.nodes().iter().cloned(),
        body.edges().iter().cloned(),
        body.interface().declaration().clone(),
    )
    .map_err(invalid)?;
    let edges = [
        Edge::results([&read], &double).map_err(invalid)?,
        Edge::results([&read, &double], &add).map_err(invalid)?,
        // 前面的双参数结果边展开后占两条；条件的结构位置必须保持正确。
        Edge::with_condition(&add, &wait, condition.clone()),
        Edge::ports_with_condition(
            gated
                .interface()
                .output(PortName::Anonymous)
                .map_err(invalid)?,
            target
                .interface()
                .input(PortName::Anonymous)
                .map_err(invalid)?,
            condition,
        ),
    ];
    let interface = GraphInterface::default()
        .input(PortName::Anonymous, [&read])
        .map_err(invalid)?
        .output("sum", &add)
        .map_err(invalid)?;
    export_node(
        py,
        Graph::with_interface(
            "conditions",
            [
                read.into(),
                double.into(),
                add.into(),
                wait.into(),
                gated.into(),
                target.into(),
            ],
            edges,
            interface,
        )
        .map_err(invalid)?,
    )
}

struct Seed;
impl Action for Seed {
    type Inputs = ();
    type Output = i64;
    fn execute(&self, _: &ActionContext, (): ()) -> Result<i64, ActionError> {
        Ok(21)
    }
}

#[pyfunction]
fn make_port_conditions(py: Python<'_>) -> PyResult<Py<PyAny>> {
    let seed = Step::new("seed", ActionHandle::new(Seed)).map_err(invalid)?;
    let source = Graph::with_interface(
        "source",
        [seed.clone().into()],
        [],
        GraphInterface::default()
            .output(PortName::Anonymous, &seed)
            .map_err(invalid)?,
    )
    .map_err(invalid)?;
    let forwarded = Graph::with_interface(
        "forwarded",
        [source.clone().into()],
        [],
        GraphInterface::default()
            .output(
                PortName::Anonymous,
                source
                    .interface()
                    .output(PortName::Anonymous)
                    .map_err(invalid)?,
            )
            .map_err(invalid)?,
    )
    .map_err(invalid)?;
    let output = forwarded
        .interface()
        .output(PortName::Anonymous)
        .map_err(invalid)?;
    let condition = Condition::new(
        |(value,): (Option<Arc<i64>>,)| Ok(value.is_some_and(|value| *value == 21)),
        [output.clone().into()],
    )
    .map_err(invalid)?;
    let target =
        Step::with_condition("double", ActionHandle::new(Double), condition).map_err(invalid)?;
    let edges = [Edge::results([output], &target).map_err(invalid)?];
    let interface = GraphInterface::default()
        .output(PortName::Anonymous, &target)
        .map_err(invalid)?;
    // 特意让消费者先声明，验证引用恢复不依赖节点声明顺序。
    export_node(
        py,
        Graph::with_interface(
            "port conditions",
            [target.into(), forwarded.into()],
            edges,
            interface,
        )
        .map_err(invalid)?,
    )
}

#[pyfunction]
fn make_conditional_step(py: Python<'_>, allowed: bool) -> PyResult<Py<PyAny>> {
    let predicate = Condition::new(move |()| Ok(allowed), []).map_err(invalid)?;
    export_node(
        py,
        Step::with_condition("seed", ActionHandle::new(Seed), predicate).map_err(invalid)?,
    )
}

#[pyfunction]
fn make_conditional_graph(py: Python<'_>, allowed: bool) -> PyResult<Py<PyAny>> {
    let predicate = Condition::new(move |()| Ok(allowed), []).map_err(invalid)?;
    let seed = Step::new("seed", ActionHandle::new(Seed)).map_err(invalid)?;
    let body = Graph::with_interface(
        "scope",
        [seed.clone().into()],
        [],
        GraphInterface::default()
            .output(PortName::Anonymous, &seed)
            .map_err(invalid)?,
    )
    .map_err(invalid)?;
    export_node(
        py,
        Graph::with_condition(
            "gated",
            body.nodes().iter().cloned(),
            body.edges().iter().cloned(),
            body.interface().declaration().clone(),
            predicate,
        )
        .map_err(invalid)?,
    )
}
