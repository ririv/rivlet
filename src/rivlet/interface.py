from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from ._native import resolve_port_connections
from .errors import GraphError

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from .model import Edge, Graph, Node, Step


type PortName = str | None


@dataclass(frozen=True, slots=True)
class InputPort:
    """An input port belonging to one concrete graph node."""

    graph: Graph
    name: PortName

    @property
    def nodes(self) -> tuple[Node, ...]:
        """Inspect declared consumers without discarding this port's owner."""

        return tuple(
            node
            for target in _declared_interface(self.graph).inputs[self.name]
            for node in (target.nodes if isinstance(target, InputPort) else (target,))
        )


@dataclass(frozen=True, slots=True)
class OutputPort:
    """An output port belonging to one concrete graph node."""

    graph: Graph
    name: PortName


type InputEndpoint = Node | InputPort
type OutputEndpoint = Node | OutputPort
type InterfaceInputs[Name: PortName] = (
    Mapping[Name, InputEndpoint | tuple[InputEndpoint, ...]]
    | Graph
    | Step
    | InputPort
    | tuple[InputEndpoint, ...]
)
type InterfaceOutputs[Name: PortName] = (
    Mapping[Name, OutputEndpoint] | Graph | Step | OutputPort
)
type GraphInterfaceInput = GraphInterface | Graph


def _validate_port_name(name: PortName) -> None:
    if name is not None and (not name or name != name.strip()):
        raise GraphError("port name must be non-empty and stripped")


@dataclass(frozen=True, slots=True, init=False)
class GraphInterface:
    """An immutable public interface with explicit input and output forwarding."""

    inputs: Mapping[PortName, tuple[InputEndpoint, ...]]
    outputs: Mapping[PortName, OutputEndpoint]

    def __init__[InputName: PortName, OutputName: PortName](
        self,
        *,
        inputs: InterfaceInputs[InputName] = MappingProxyType({}),
        outputs: InterfaceOutputs[OutputName] = MappingProxyType({}),
    ) -> None:
        from .model import Graph

        # Graph 仍表示接口转发；把子图本体作为匿名端点时显式写 {None: child}。
        input_mapping = (
            inputs.interface.inputs
            if isinstance(inputs, Graph)
            else inputs
            if isinstance(inputs, Mapping)
            else {None: inputs}
        )
        output_mapping = (
            outputs.interface.outputs
            if isinstance(outputs, Graph)
            else outputs
            if isinstance(outputs, Mapping)
            else {None: outputs}
        )
        normalized_inputs: dict[PortName, tuple[InputEndpoint, ...]] = {}
        # O(P + R)：P 为端口数，R 为全部输入步骤引用数；复制映射隔离调用方修改。
        for port, value in input_mapping.items():
            _validate_port_name(port)
            steps = value if isinstance(value, tuple) else (value,)
            if not steps:
                raise GraphError(f"port input must reference at least one step: {port}")
            if len(set(steps)) != len(steps):
                raise GraphError(f"port input contains duplicate steps: {port}")
            normalized_inputs[port] = steps
        normalized_outputs: dict[PortName, OutputEndpoint] = {}
        for port, target in output_mapping.items():
            _validate_port_name(port)
            normalized_outputs[port] = target
        object.__setattr__(self, "inputs", MappingProxyType(normalized_inputs))
        object.__setattr__(self, "outputs", MappingProxyType(normalized_outputs))

    @classmethod
    def resolve(cls, value: GraphInterfaceInput) -> GraphInterface:
        from .model import Graph

        return (
            cls(inputs=value.interface.inputs, outputs=value.interface.outputs)
            if isinstance(value, Graph)
            else value
        )


class _Inputs(Mapping[PortName, InputPort]):
    def __init__(self, graph: Graph) -> None:
        self._graph = graph

    def __getitem__(self, key: PortName) -> InputPort:
        from ._native import RustGraphStructure

        body = self._graph.structure
        exists = (
            body.has_input_port(key)
            if isinstance(body, RustGraphStructure)
            else key in body.interface.inputs
        )
        if not exists:
            raise KeyError(key)
        return InputPort(self._graph, key)

    @property
    def _names(self) -> Mapping[PortName, tuple[InputEndpoint, ...]] | list[PortName]:
        from ._native import RustGraphStructure

        body = self._graph.structure
        return (
            body.input_ports()
            if isinstance(body, RustGraphStructure)
            else body.interface.inputs
        )

    def __len__(self) -> int:
        return len(self._names)

    def __iter__(self) -> Iterator[PortName]:
        return iter(self._names)


class _Outputs(Mapping[PortName, OutputPort]):
    def __init__(self, graph: Graph) -> None:
        self._graph = graph

    def __getitem__(self, key: PortName) -> OutputPort:
        from ._native import RustGraphStructure

        body = self._graph.structure
        exists = (
            body.has_output_port(key)
            if isinstance(body, RustGraphStructure)
            else key in body.interface.outputs
        )
        if not exists:
            raise KeyError(key)
        return OutputPort(self._graph, key)

    @property
    def _names(self) -> Mapping[PortName, OutputEndpoint] | list[PortName]:
        from ._native import RustGraphStructure

        body = self._graph.structure
        return (
            body.output_ports()
            if isinstance(body, RustGraphStructure)
            else body.interface.outputs
        )

    def __len__(self) -> int:
        return len(self._names)

    def __iter__(self) -> Iterator[PortName]:
        return iter(self._names)


@dataclass(frozen=True, slots=True)
class GraphInterfaceView:
    """Expose ports with their owning graph identity, without a separate bind API."""

    graph: Graph

    @property
    def inputs(self) -> Mapping[PortName, InputPort]:
        return _Inputs(self.graph)

    @property
    def outputs(self) -> Mapping[PortName, OutputPort]:
        return _Outputs(self.graph)

    @property
    def input(self) -> InputPort:
        """Return the explicitly declared anonymous input, without name inference."""
        return self.inputs[None]

    @property
    def output(self) -> OutputPort:
        """Return the explicitly declared anonymous output, without name inference."""
        return self.outputs[None]


def _declared_interface(graph: Graph) -> GraphInterface:
    from .model.graph_structure import GraphStructure

    if not isinstance(graph.structure, GraphStructure):
        raise GraphError("opaque Rust interfaces do not expose Python node objects")
    return graph.structure.interface


@dataclass(frozen=True, slots=True)
class AnyProviderRequirement:
    """A consumer requirement that does not name its concrete provider."""

    consumer: Node
    port: str

    def __post_init__(self) -> None:
        _validate_port_name(self.port)


@dataclass(frozen=True, slots=True)
class ExactProviderRequirement:
    """A consumer requirement bound by declaration to one concrete provider."""

    consumer: Node
    provider: Node
    port: str

    def __post_init__(self) -> None:
        _validate_port_name(self.port)


type PortRequirement = AnyProviderRequirement | ExactProviderRequirement


@dataclass(frozen=True, slots=True)
class ProviderSelection:
    """A deterministic provider selection for an any-provider requirement."""

    requirement: AnyProviderRequirement
    provider: Node


def resolve_port_requirements(
    components: Iterable[Node],
    requirements: Iterable[PortRequirement],
    *,
    selections: Iterable[ProviderSelection] = (),
) -> tuple[Edge, ...]:
    """Resolve symbolic requirements into deterministic port connections."""

    from .model import Edge

    mounted_components = tuple(components)
    mounted_set = set(mounted_components)
    component_indexes = {
        component: index for index, component in enumerate(mounted_components)
    }
    component_graphs = tuple(
        _require_component_graph(component) for component in mounted_components
    )
    declared_requirements = tuple(requirements)
    declared_selections = tuple(selections)

    ports: dict[str, int] = {}
    for graph in component_graphs:
        for port in graph.interface.outputs:
            if port is not None:
                ports.setdefault(port, len(ports))
    for requirement in declared_requirements:
        ports.setdefault(requirement.port, len(ports))

    any_requirements: list[tuple[int, int, int]] = []
    exact_requirements: list[tuple[int, int, int, int]] = []
    for index, requirement in enumerate(declared_requirements):
        _require_mounted_component(
            requirement.consumer,
            mounted_set,
            role="consumer",
        )
        _require_declared_port_input(
            requirement.consumer,
            requirement.port,
        )
        match requirement:
            case ExactProviderRequirement(provider=provider):
                _require_mounted_component(provider, mounted_set, role="provider")
                _require_port_output(provider, requirement.port)
                exact_requirements.append(
                    (
                        index,
                        component_indexes[requirement.consumer],
                        ports[requirement.port],
                        component_indexes[provider],
                    )
                )
            case AnyProviderRequirement():
                any_requirements.append(
                    (
                        index,
                        component_indexes[requirement.consumer],
                        ports[requirement.port],
                    )
                )

    resolved_selections = [
        (
            _requirement_index(declared_requirements, selection.requirement),
            component_indexes.get(selection.provider, len(mounted_components)),
        )
        for selection in declared_selections
    ]
    component_outputs = [
        [ports[port] for port in graph.interface.outputs if port is not None]
        for graph in component_graphs
    ]
    try:
        resolved = resolve_port_connections(
            component_outputs,
            len(declared_requirements),
            any_requirements,
            exact_requirements,
            resolved_selections,
        )
    except ValueError as error:
        raise GraphError(str(error)) from error

    ports_by_index = tuple(ports)
    return tuple(
        Edge(
            source=component_graphs[provider],
            target=component_graphs[consumer],
            ports=ports_by_index[port],
        )
        for provider, consumer, port in resolved
    )


def _requirement_index(
    requirements: tuple[PortRequirement, ...],
    selected: AnyProviderRequirement,
) -> int:
    for index, requirement in enumerate(requirements):
        if requirement == selected:
            return index
    # 交由 Rust 核心统一报告未知 selection；这里不复制结构校验。
    return len(requirements)


def _require_mounted_component(
    component: Node,
    mounted_components: set[Node],
    *,
    role: str,
) -> None:
    if component not in mounted_components:
        raise GraphError(
            f"port {role} is not mounted in this composition: {_step_label(component)}"
        )


def _require_component_graph(component: Node) -> Graph:
    from .model import Graph

    if not isinstance(component, Graph):
        raise GraphError(
            f"port component must be a Graph node: {_step_label(component)}"
        )
    return component


def _require_declared_port_input(
    component: Node,
    port: PortName,
) -> None:
    graph = _require_component_graph(component)
    if port in graph.interface.inputs:
        return
    raise GraphError(f"component {_step_label(component)} does not require port {port}")


def _require_port_output(
    component: Node,
    port: PortName,
) -> OutputPort:
    graph = _require_component_graph(component)
    if port in graph.interface.outputs:
        return graph.interface.outputs[port]
    raise GraphError(f"component {_step_label(component)} does not provide port {port}")


def _step_label(step: Node) -> str:
    return "<unlabeled>" if step.label is None else step.label
