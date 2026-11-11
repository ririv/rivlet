from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from ._native import resolve_port_connections
from .errors import GraphError

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .model import Graph, Step


type InterfaceInputs = Mapping[str, Step | tuple[Step, ...]] | Graph
type InterfaceOutputs = Mapping[str, Step] | Graph
type GraphInterfaceInput = GraphInterface | Graph


def _validate_port_name(name: str) -> None:
    if not name or name != name.strip():
        raise GraphError("port name must be non-empty and stripped")


@dataclass(frozen=True, slots=True, init=False)
class GraphInterface:
    """An immutable public interface with explicit input and output forwarding."""

    inputs: Mapping[str, tuple[Step, ...]]
    outputs: Mapping[str, Step]

    def __init__(
        self,
        *,
        inputs: InterfaceInputs = MappingProxyType({}),
        outputs: InterfaceOutputs = MappingProxyType({}),
    ) -> None:
        from .model import Graph, Step

        input_mapping = inputs.interface.inputs if isinstance(inputs, Graph) else inputs
        output_mapping = (
            outputs.interface.outputs if isinstance(outputs, Graph) else outputs
        )
        normalized_inputs = {}
        # O(P + R)：P 为端口数，R 为全部输入步骤引用数；复制映射隔离调用方修改。
        for port, value in input_mapping.items():
            _validate_port_name(port)
            steps = (value,) if isinstance(value, Step) else value
            if not steps:
                raise GraphError(f"port input must reference at least one step: {port}")
            if len(set(steps)) != len(steps):
                raise GraphError(f"port input contains duplicate steps: {port}")
            normalized_inputs[port] = steps
        for port in output_mapping:
            _validate_port_name(port)
        object.__setattr__(self, "inputs", MappingProxyType(normalized_inputs))
        object.__setattr__(self, "outputs", MappingProxyType(dict(output_mapping)))

    @classmethod
    def resolve(cls, value: GraphInterfaceInput) -> GraphInterface:
        from .model import Graph

        return value.interface if isinstance(value, Graph) else value


@dataclass(frozen=True, slots=True)
class AnyProviderRequirement:
    """A consumer requirement that does not name its concrete provider."""

    consumer: Step
    port: str

    def __post_init__(self) -> None:
        _validate_port_name(self.port)


@dataclass(frozen=True, slots=True)
class ExactProviderRequirement:
    """A consumer requirement bound by declaration to one concrete provider."""

    consumer: Step
    provider: Step
    port: str

    def __post_init__(self) -> None:
        _validate_port_name(self.port)


type PortRequirement = AnyProviderRequirement | ExactProviderRequirement


@dataclass(frozen=True, slots=True)
class ProviderSelection:
    """A deterministic provider selection for an any-provider requirement."""

    requirement: AnyProviderRequirement
    provider: Step


@dataclass(frozen=True, slots=True)
class PortConnection:
    """A resolved port connection between two mounted graph components."""

    source: Step
    target: Step
    port: str

    def __post_init__(self) -> None:
        _validate_port_name(self.port)


def resolve_port_requirements(
    components: Iterable[Step],
    requirements: Iterable[PortRequirement],
    *,
    selections: Iterable[ProviderSelection] = (),
) -> tuple[PortConnection, ...]:
    """Resolve symbolic requirements into deterministic port connections."""

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

    native_selections = [
        (
            _requirement_index(declared_requirements, selection.requirement),
            component_indexes.get(selection.provider, len(mounted_components)),
        )
        for selection in declared_selections
    ]
    component_outputs = [
        [ports[port] for port in graph.interface.outputs] for graph in component_graphs
    ]
    try:
        resolved = resolve_port_connections(
            component_outputs,
            len(declared_requirements),
            any_requirements,
            exact_requirements,
            native_selections,
        )
    except ValueError as error:
        raise GraphError(str(error)) from error

    ports_by_index = tuple(ports)
    return tuple(
        PortConnection(
            source=mounted_components[provider],
            target=mounted_components[consumer],
            port=ports_by_index[port],
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
    component: Step,
    mounted_components: set[Step],
    *,
    role: str,
) -> None:
    if component not in mounted_components:
        raise GraphError(
            f"port {role} is not mounted in this composition: {_step_label(component)}"
        )


def _require_component_graph(component: Step) -> Graph:
    from .model import Graph

    if not isinstance(component.action, Graph):
        raise GraphError(
            f"port component must be a graph-valued step: {_step_label(component)}"
        )
    return component.action


def _require_declared_port_input(
    component: Step,
    port: str,
) -> None:
    graph = _require_component_graph(component)
    if port in graph.interface.inputs:
        return
    raise GraphError(f"component {_step_label(component)} does not require port {port}")


def _require_port_output(
    component: Step,
    port: str,
) -> Step:
    graph = _require_component_graph(component)
    if port in graph.interface.outputs:
        return graph.interface.outputs[port]
    raise GraphError(f"component {_step_label(component)} does not provide port {port}")


def _step_label(step: Step) -> str:
    return "<unlabeled>" if step.label is None else step.label
