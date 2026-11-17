from collections.abc import Callable
from enum import Enum
from typing import Literal, Protocol

from .model.graph import Graph
from .model.step import Step

def _import_node(capsule: object) -> Step | Graph: ...

class _ResultFixtureKind(Enum):
    I64 = 0
    U64 = 1
    Bool = 2
    F64 = 3
    String = 4
    Bytes = 5
    Unit = 6
    Custom = 7
    Opaque = 8

def _rust_result_fixture(
    kind: _ResultFixtureKind,
) -> tuple[Graph, Graph]: ...
def _rust_condition_fixture() -> Graph: ...

class _NativePredicate: ...

class _NativeStep:
    def input_count(self) -> int: ...

class _NativeCondition: ...

type EncodedExecutionEvent = (
    tuple[Literal[16]]
    | tuple[Literal[0], int, bool]
    | tuple[Literal[1, 2, 3, 4, 5, 6, 7, 15], int]
    | tuple[Literal[8, 9, 11, 12], int, int]
    | tuple[Literal[10, 13], int, int, int]
    | tuple[Literal[14], int, int, int, int]
)

type NativeEndpoint = tuple[int, tuple[str | None] | None]
type NativeEdge = (
    tuple[Literal[0], int, int, int | None]
    | tuple[Literal[1], int, int, str | None, str | None, int | None]
    | tuple[Literal[2], list[NativeEndpoint], NativeEndpoint]
)

class _NativeGraph:
    def owner_index(self) -> int | None: ...
    def node_indexes(self) -> list[int]: ...
    def node_condition(self, index: int) -> int | None: ...
    def descendants(self, indexes: list[int]) -> list[bool]: ...
    def mounted_paths(
        self, indexes: list[int]
    ) -> list[tuple[list[int], list[str]] | None]: ...
    def node_descriptor(
        self, index: int
    ) -> tuple[int, str, _NativeStep | _NativeGraph, int | None]: ...
    def condition_descriptor(
        self, index: int
    ) -> tuple[_NativeCondition, list[NativeEndpoint]]: ...
    def edge_descriptors(self) -> list[NativeEdge]: ...
    def interface_descriptor(
        self,
    ) -> tuple[
        list[tuple[str | None, list[NativeEndpoint]]],
        list[tuple[str | None, NativeEndpoint]],
    ]: ...

class RustActionBindings:
    def __init__(self, node_count: int) -> None: ...
    def python_sources(self) -> list[int]: ...

class _ConcurrencyLimits:
    def __init__(self, maxima: list[int]) -> None: ...
    def revision(self) -> int: ...

class _RustResult:
    def to_python(self) -> object: ...

class ExperimentalCallbackHost(Protocol):
    def skipped_nodes(self) -> list[int]: ...
    def resolve_conditions(
        self,
        requests: list[tuple[int, int]],
    ) -> list[tuple[int, bool | None]]: ...
    def raise_failures(self) -> None: ...
    def apply_events(self, events: list[EncodedExecutionEvent]) -> None: ...
    def run_step(self, node: int) -> object: ...
    def stop_requested(self) -> bool: ...
    def request_stop(self) -> None: ...
    def require_running(self) -> None: ...

def allocate_node_id() -> int: ...
def dag_cycle(
    node_count: int,
    arcs: list[tuple[int, int]],
) -> list[int] | None: ...
def resolve_port_connections(
    component_outputs: list[list[int]],
    requirement_count: int,
    any_requirements: list[tuple[int, int, int]],
    exact_requirements: list[tuple[int, int, int, int]],
    selections: list[tuple[int, int]],
) -> list[tuple[int, int, int]]: ...

class DeclarationCompilation:
    # 节点/条件侧表按编译索引排列，仅包含存活项；内部引用仍用原声明 ID。
    # graph_* 侧表保持声明图索引，供原作用域与祖先引用使用。
    @property
    def graph(self) -> CompiledExecutionGraph: ...
    def declaration_nodes(self) -> list[int]: ...
    def declaration_conditions(self) -> list[int]: ...
    def node_ids(self) -> list[int]: ...
    def node_paths(self) -> list[list[int]]: ...
    def node_labels(self) -> list[str]: ...
    def node_graphs(self) -> list[int]: ...
    def node_child_graphs(self) -> list[int | None]: ...
    def source_steps(self) -> list[object | None]: ...
    def actions(self) -> list[object | None]: ...
    def rust_actions(self) -> RustActionBindings: ...
    def graph_labels(self) -> list[str]: ...
    def graph_label_paths(self) -> list[list[str]]: ...
    def graph_owners(self) -> list[int | None]: ...
    def graph_local_nodes(self) -> list[list[int]]: ...
    def source_graphs(self) -> list[object]: ...
    def predicates(self) -> list[object]: ...
    def condition_reads(self) -> list[list[int]]: ...
    def node_inputs(self) -> list[list[int]]: ...
    def export_origins(
        self,
    ) -> tuple[
        list[int],
        list[tuple[list[int] | None, int]],
        list[tuple[list[int], int | None]],
    ]: ...

def compile_declaration(
    source_graph: object,
    root_label: str,
    root_label_path: list[str] = ...,
    id_prefix: list[int] = ...,
) -> DeclarationCompilation: ...
def _rust_process_graph(
    processes: list[tuple[str, str, list[str], str, dict[str, str]]],
    arcs: list[tuple[int, int]],
    inputs: list[tuple[str | None, list[int]]] = ...,
    outputs: list[tuple[str | None, int]] = ...,
) -> Graph: ...
def _compose_rust_graph(
    components: list[tuple[str, Graph]],
    edges: list[
        tuple[int, int]
        | tuple[int, int, str | None, str | None]
        | tuple[list[tuple[int, str | None]], tuple[int, str | None]]
    ],
    inputs: list[tuple[str | None, list[tuple[int, str | None]]]],
    outputs: list[tuple[str | None, int, str | None]],
) -> Graph: ...
def _experimental_run_tokio_callbacks(
    graph: CompiledExecutionGraph,
    host: ExperimentalCallbackHost,
    concurrency: int,
) -> None: ...

class CompiledExecutionGraph:
    def __init__(
        self,
        node_graphs: list[int],
        composite_child_graphs: list[tuple[int, int]],
        graph_owners: list[int | None],
        prerequisites: list[list[tuple[int, int | None]]],
        node_conditions: list[int | None],
        condition_reads: list[list[int]],
        port_connections: list[tuple[int, int]],
    ) -> None: ...
    def roots(self) -> list[int]: ...
    def children(self, node: int) -> list[int]: ...
    def dependents(self, node: int) -> list[int]: ...
    def all_prerequisites(self) -> list[list[tuple[int, int | None]]]: ...
    def node_conditions(self) -> list[int | None]: ...
    def topological_order(self) -> list[int]: ...
    def session(self, initially_skipped: list[int]) -> ExecutionSession: ...
    def mixed_session(
        self,
        initially_skipped: list[int],
        rust_actions: RustActionBindings,
        rust_contexts: list[tuple[int, str | None]],
        workers: int | None = ...,
        retain_results: bool = ...,
        concurrency: tuple[_ConcurrencyLimits, list[list[int]]] | None = ...,
    ) -> _DriverSession: ...

class ExecutionBatch:
    def parts(
        self,
    ) -> tuple[
        list[int],
        list[tuple[int, int]],
        list[EncodedExecutionEvent],
        bool,
    ]: ...

class ExecutionSession:
    def request_cancel(self) -> None: ...
    def start(self, max_actions: int) -> ExecutionBatch: ...
    def advance(
        self,
        completed: list[tuple[int, bool]],
        conditions: list[tuple[int, bool | None]],
        skipped: list[int],
        max_actions: int,
        cancelled: list[int] = ...,
        cancelled_conditions: list[int] = ...,
    ) -> ExecutionBatch: ...

class MixedExecutionBatch:
    def parts(
        self,
    ) -> tuple[
        list[int],
        list[tuple[int, int]],
        list[EncodedExecutionEvent],
        bool,
        int,
        list[tuple[int, str]],
        list[tuple[int, _RustResult]],
    ]: ...

class MixedExecutionSession:
    def request_cancel(self) -> None: ...
    def suspend_action(self, node: int) -> list[int]: ...
    def start(self, capacity: int) -> MixedExecutionBatch: ...
    def advance(
        self,
        completed: list[tuple[int, bool]],
        conditions: list[tuple[int, bool | None]],
        skipped: list[int],
        available: int,
        values: list[tuple[int, object]] = ...,
        cancelled: list[int] = ...,
        cancelled_conditions: list[int] = ...,
    ) -> MixedExecutionBatch: ...
    def wait_rust(self) -> None: ...
    def notify_rust(self, callback: Callable[[], None]) -> None: ...

class HostExecutionBatch:
    def parts(
        self,
    ) -> tuple[
        list[int],
        list[tuple[int, int]],
        list[EncodedExecutionEvent],
        bool,
        int,
        list[tuple[int, str]],
        list[tuple[int, _RustResult]],
    ]: ...

class HostExecutionSession:
    def request_cancel(self) -> None: ...
    def suspend_action(self, node: int) -> list[int]: ...
    def start(self, capacity: int) -> HostExecutionBatch: ...
    def advance(
        self,
        completed: list[tuple[int, bool]],
        conditions: list[tuple[int, bool | None]],
        skipped: list[int],
        available: int,
        values: list[tuple[int, object]] = ...,
        cancelled: list[int] = ...,
        cancelled_conditions: list[int] = ...,
    ) -> HostExecutionBatch: ...
    def wait_rust(self) -> None: ...
    def notify_rust(self, callback: Callable[[], None]) -> None: ...

type _DriverSession = HostExecutionSession | MixedExecutionSession
type _DriverBatch = HostExecutionBatch | MixedExecutionBatch
