from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from ..errors import GraphError
from .mermaid import render_graph_mermaid
from .tree import render_graph

if TYPE_CHECKING:
    from ..execution import Execution
    from ..model import GraphMode

type GraphRenderFormat = Literal["tree", "mermaid"]


@dataclass(frozen=True, slots=True)
class GraphRenderOptions:
    mode: GraphMode = "dynamic"
    fmt: GraphRenderFormat = "tree"


def render_execution_structure(
    execution: Execution,
    *,
    options: GraphRenderOptions,
) -> str:
    match options.fmt:
        case "tree":
            return render_graph(
                execution._declaration_graph,
                root_label=execution._root_label,
                mode=options.mode,
            )
        case "mermaid":
            return render_graph_mermaid(
                execution._declaration_graph,
                root_label=execution._root_label,
                mode=options.mode,
            )
        case _:
            raise GraphError(f"unsupported graph render format: {options.fmt}")


def resolve_graph_render_options(
    *,
    enabled: bool,
    mode: GraphMode | None,
    fmt: GraphRenderFormat | None,
) -> GraphRenderOptions | None:
    if not enabled:
        if mode is not None or fmt is not None:
            raise ValueError("--graph-mode 和 --graph-format 只能和 --graph 一起使用")
        return None
    return GraphRenderOptions(
        mode="dynamic" if mode is None else mode,
        fmt="tree" if fmt is None else fmt,
    )
